"""
mcp_server/mqtt_subscriber.py
------------------------------
Live MQTT subscriber for the BioT Sensor Assistant.

Responsibilities:
- Connect to the Mosquitto broker (same one the Android app and ESP8266 use)
- Subscribe to all sensor topics
- Parse incoming payloads
- Write every reading into data/sensor_database.db using the same schema
  as the Android Room database so the LLM agent can query live data

Topics subscribed:
    Sensor/Mic       — KY-037 microphone (integer 0-1023)
    Sensor/Bewegung  — MPU-6050 accelerometer (x,y,z floats in g)
    Sensor/Gyro      — MPU-6050 gyroscope (x,y,z floats in deg/s)
    Sensor/Magnet    — A3144 hall effect sensor (x,y,z floats)

Database schema (mirrors Android Room DB exactly):
    accel_data   — id, timestamp, accelX, accelY, accelZ
    gyro_data    — id, timestamp, gyroX, gyroY, gyroZ
    magnet_data  — id, timestamp, magnetX, magnetY, magnetZ
    ereignis_data — id, timestamp, sensorType, value, axis

Note: Sensor/Mic is NOT written to the DB (high frequency, display-only
in the Android app). This matches the Android app behaviour.

Usage:
    Started automatically by app/main.py on server startup.
    Can also be run standalone for testing:
        uv run python -m mcp_server.mqtt_subscriber
"""

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from app.logger import get_logger

log = get_logger(__name__)

# ── MQTT Topics ───────────────────────────────────────────────────────────────
_TOPICS = [
    "Sensor/Mic",
    "Sensor/Bewegung",
    "Sensor/Gyro",
    "Sensor/Magnet",
]

_RECONNECT_DELAY_SECS = 5


class SensorMqttSubscriber:
    """
    Background MQTT subscriber that writes live sensor readings into SQLite.

    Designed to run in a daemon thread alongside the FastAPI server.
    Handles connection loss and reconnects automatically.

    Args:
        broker_host: Mosquitto broker hostname or IP (injected from settings).
        broker_port: Mosquitto broker port (injected from settings).
        db_path:     Absolute path to the SQLite sensor database file.
    """

    def __init__(self, broker_host: str, broker_port: int, db_path: Path) -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._db_path = db_path
        self._client: Optional[mqtt.Client] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        log.info(
            "SensorMqttSubscriber configured (broker=%s:%d, db=%s)",
            broker_host, broker_port, db_path,
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Start the subscriber in a background daemon thread.
        Returns immediately — the subscriber runs independently.
        """
        if self._running:
            log.warning("SensorMqttSubscriber already running")
            return

        self._running = True
        self._ensure_db_schema()

        self._thread = threading.Thread(
            target=self._run_loop,
            name="mqtt-subscriber",
            daemon=True,
        )
        self._thread.start()
        log.info("SensorMqttSubscriber started in background thread")

    def stop(self) -> None:
        """Signal the subscriber to stop and disconnect from the broker."""
        log.info("Stopping SensorMqttSubscriber")
        self._running = False
        if self._client:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception as exc:
                log.warning("Error during MQTT disconnect: %s", exc)

    # ── Background loop ───────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main loop running in background thread. Reconnects on failure."""
        while self._running:
            try:
                self._connect_and_run()
            except Exception as exc:
                log.error(
                    "MQTT subscriber error: %s — retrying in %ds",
                    exc, _RECONNECT_DELAY_SECS,
                )
                time.sleep(_RECONNECT_DELAY_SECS)

    def _connect_and_run(self) -> None:
        """Create a new MQTT client, connect, and block until disconnected."""
        self._client = mqtt.Client(
            client_id="biot-llm-subscriber",
            protocol=mqtt.MQTTv5,
        )

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        log.info(
            "Connecting to MQTT broker at %s:%d",
            self._broker_host, self._broker_port,
        )
        self._client.connect(self._broker_host, self._broker_port, keepalive=120)
        self._client.loop_forever()

    # ── MQTT callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            log.info("Connected to MQTT broker — subscribing to sensor topics")
            for topic in _TOPICS:
                client.subscribe(topic, qos=1)
                log.debug("Subscribed: %s", topic)
        else:
            log.warning("MQTT connection refused — reason code: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self._running:
            log.warning(
                "Disconnected from MQTT broker (rc=%s) — will reconnect", reason_code
            )
        else:
            log.info("Disconnected from MQTT broker (clean shutdown)")

    def _on_message(self, client, userdata, msg) -> None:
        """Parse every incoming MQTT message and write to the appropriate DB table."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        log.debug("MQTT message: %s = %r", topic, payload[:80])

        try:
            if topic == "Sensor/Bewegung":
                self._write_accel(payload)
            elif topic == "Sensor/Gyro":
                self._write_gyro(payload)
            elif topic == "Sensor/Magnet":
                self._write_magnet(payload)
            elif topic == "Sensor/Mic":
                # Mic data is display-only — not persisted, matches Android behaviour
                log.debug("Mic level: %s (not persisted)", payload)
            else:
                log.warning("Unhandled topic: %s", topic)
        except Exception as exc:
            log.error(
                "Error processing message on %s: %s", topic, exc, exc_info=True
            )

    # ── DB writers ────────────────────────────────────────────────────────────

    def _write_accel(self, payload: str) -> None:
        """
        Parse accelerometer payload and write to accel_data.
        Handles both stream format ("1.23,-0.45,9.81") and
        burst format ("1.23,-0.45,9.81,1.24,-0.46,9.80,...").
        """
        parts = [p.strip() for p in payload.split(",")]
        triplets = [parts[i:i+3] for i in range(0, len(parts) - 2, 3)]
        for triplet in triplets:
            try:
                x, y, z = float(triplet[0]), float(triplet[1]), float(triplet[2])
                self._db_insert(
                    "INSERT INTO accel_data (timestamp, accelX, accelY, accelZ) VALUES (?,?,?,?)",
                    (self._now_ms(), x, y, z),
                )
                log.debug("Wrote accel: x=%.3f y=%.3f z=%.3f", x, y, z)
            except (ValueError, IndexError) as exc:
                log.warning("Accel parse error for %s: %s", triplet, exc)

    def _write_gyro(self, payload: str) -> None:
        """Parse gyroscope payload and write to gyro_data."""
        parts = [p.strip() for p in payload.split(",")]
        triplets = [parts[i:i+3] for i in range(0, len(parts) - 2, 3)]
        for triplet in triplets:
            try:
                x, y, z = float(triplet[0]), float(triplet[1]), float(triplet[2])
                self._db_insert(
                    "INSERT INTO gyro_data (timestamp, gyroX, gyroY, gyroZ) VALUES (?,?,?,?)",
                    (self._now_ms(), x, y, z),
                )
                log.debug("Wrote gyro: x=%.3f y=%.3f z=%.3f", x, y, z)
            except (ValueError, IndexError) as exc:
                log.warning("Gyro parse error for %s: %s", triplet, exc)

    def _write_magnet(self, payload: str) -> None:
        """Parse magnetometer payload and write to magnet_data."""
        parts = [p.strip() for p in payload.split(",")]
        triplets = [parts[i:i+3] for i in range(0, len(parts) - 2, 3)]
        for triplet in triplets:
            try:
                x, y, z = float(triplet[0]), float(triplet[1]), float(triplet[2])
                self._db_insert(
                    "INSERT INTO magnet_data (timestamp, magnetX, magnetY, magnetZ) VALUES (?,?,?,?)",
                    (self._now_ms(), x, y, z),
                )
                log.debug("Wrote magnet: x=%.3f y=%.3f z=%.3f", x, y, z)
            except (ValueError, IndexError) as exc:
                log.warning("Magnet parse error for %s: %s", triplet, exc)

    # ── SQLite helpers ────────────────────────────────────────────────────────

    def _ensure_db_schema(self) -> None:
        """
        Create the database tables if they do not exist yet.
        Schema mirrors the Android Room database exactly so the agent's
        SQL queries work identically against both databases.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        ddl = [
            """CREATE TABLE IF NOT EXISTS accel_data (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                accelX    REAL    NOT NULL,
                accelY    REAL    NOT NULL,
                accelZ    REAL    NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS gyro_data (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                gyroX     REAL    NOT NULL,
                gyroY     REAL    NOT NULL,
                gyroZ     REAL    NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS magnet_data (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                magnetX   REAL    NOT NULL,
                magnetY   REAL    NOT NULL,
                magnetZ   REAL    NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS ereignis_data (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  INTEGER NOT NULL,
                sensorType TEXT    NOT NULL,
                value      REAL    NOT NULL,
                axis       TEXT    NOT NULL
            )""",
        ]

        try:
            conn = sqlite3.connect(str(self._db_path))
            for statement in ddl:
                conn.execute(statement)
            conn.commit()
            conn.close()
            log.info("Database schema ready: %s", self._db_path)
        except sqlite3.Error as exc:
            log.error("Failed to initialise DB schema: %s", exc, exc_info=True)
            raise

    def _db_insert(self, sql: str, params: tuple) -> None:
        """
        Execute a single INSERT.
        Opens and closes a connection per call — safe for concurrent access
        from the subscriber thread alongside the agent's read queries.
        """
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(sql, params)
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            log.error("DB insert failed: %s", exc)

    @staticmethod
    def _now_ms() -> int:
        """Current time in milliseconds — matches Android System.currentTimeMillis()."""
        return int(time.time() * 1000)


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    from config.settings import settings

    print(f"Starting standalone MQTT subscriber")
    print(f"Broker : {settings.mqtt_broker_host}:{settings.mqtt_broker_port}")
    print(f"DB     : {settings.sqlite_db_path}")
    print("Ctrl+C to stop")

    sub = SensorMqttSubscriber(
        broker_host=settings.mqtt_broker_host,
        broker_port=settings.mqtt_broker_port,
        db_path=settings.sqlite_db_path,
    )
    sub.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        sub.stop()