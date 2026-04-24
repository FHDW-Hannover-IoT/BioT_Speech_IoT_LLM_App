"""
mcp_server/mqtt_subscriber.py
------------------------------
Live MQTT subscriber for the BioT Sensor Assistant.

Responsibilities:
- Connect to the Mosquitto broker (same one the Android app and ESP8266 use)
- Subscribe to all sensor topics
- Parse incoming payloads
- Write every reading into the sensor database via SensorRepository
  (persistent write connection, WAL mode, ACID transactions)

Topics subscribed:
    Sensor/Bewegung  — MPU-6050 accelerometer (x,y,z floats in g)
    Sensor/Gyro      — MPU-6050 gyroscope (x,y,z floats in deg/s)
    Sensor/Magnet    — A3144 hall effect sensor (x,y,z floats)

Database writes go through SensorRepository → DbContext.execute_write()
which holds a persistent connection with WAL mode.  At 50 Hz (Stream mode)
this replaces 50 open/close cycles per second with a single reused connection.

Usage:
    Started automatically by app/main.py on server startup.
    Can also be run standalone for testing:
        uv run python -m mcp_server.mqtt_subscriber
"""

import threading
import time
import uuid as _uuid
from typing import Optional

import paho.mqtt.client as mqtt

from app.logger import get_logger
from config.settings import settings

log = get_logger(__name__)

# ── MQTT Topics ───────────────────────────────────────────────────────────────

_TOPICS = [
    "Sensor/Bewegung",
    "Sensor/Gyro",
    "Sensor/Magnet",
]


class SensorMqttSubscriber:
    """
    Background MQTT subscriber that writes live sensor readings into SQLite
    via the shared SensorRepository.

    Args:
        broker_host: Mosquitto broker hostname or IP.
        broker_port: Mosquitto broker port.
        repository:  Shared SensorRepository (owns the persistent write connection).
    """

    def __init__(self, broker_host: str, broker_port: int, repository) -> None:
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._repository = repository
        self._client: Optional[mqtt.Client] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        log.info(
            "SensorMqttSubscriber configured (broker=%s:%d)",
            broker_host, broker_port,
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the subscriber in a background daemon thread."""
        if self._running:
            log.warning("SensorMqttSubscriber already running")
            return

        self._running = True
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
        """Main loop — reconnects automatically on failure."""
        while self._running:
            try:
                self._connect_and_run()
            except Exception as exc:
                log.error(
                    "MQTT subscriber error: %s — retrying in %ds",
                    exc, settings.mqtt_reconnect_delay_secs,
                )
                time.sleep(settings.mqtt_reconnect_delay_secs)

    def _connect_and_run(self) -> None:
        """Create a new MQTT client, connect, and block until disconnected."""
        # UUID suffix prevents broker kick-off if two instances run simultaneously
        client_id = f"{settings.mqtt_client_id_prefix}-{_uuid.uuid4().hex[:8]}"

        self._client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        log.info(
            "Connecting to MQTT broker at %s:%d (client_id=%s)",
            self._broker_host, self._broker_port, client_id,
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
        """Parse every incoming MQTT message and write to the repository."""
        topic   = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        log.debug("MQTT message: %s = %r", topic, payload[:80])

        try:
            if topic == "Sensor/Bewegung":
                self._write_accel(payload)
            elif topic == "Sensor/Gyro":
                self._write_gyro(payload)
            elif topic == "Sensor/Magnet":
                self._write_magnet(payload)
            else:
                log.warning("Unhandled topic: %s", topic)
        except Exception as exc:
            log.error(
                "Error processing message on %s: %s", topic, exc, exc_info=True
            )

    # ── Payload parsers ───────────────────────────────────────────────────────

    def _write_accel(self, payload: str) -> None:
        """
        Parse accelerometer payload and write via repository.
        Handles both stream ("1.23,-0.45,9.81") and burst
        ("1.23,-0.45,9.81,1.24,-0.46,9.80,...") formats.
        """
        for x, y, z in _parse_triplets(payload):
            self._repository.insert_accel(self._now_ms(), x, y, z)

    def _write_gyro(self, payload: str) -> None:
        """Parse gyroscope payload and write via repository."""
        for x, y, z in _parse_triplets(payload):
            self._repository.insert_gyro(self._now_ms(), x, y, z)

    def _write_magnet(self, payload: str) -> None:
        """Parse magnetometer payload and write via repository."""
        for x, y, z in _parse_triplets(payload):
            self._repository.insert_magnet(self._now_ms(), x, y, z)

    @staticmethod
    def _now_ms() -> int:
        """Current time in milliseconds — matches Android System.currentTimeMillis()."""
        return int(time.time() * 1000)


# ── Payload helpers ───────────────────────────────────────────────────────────

def _parse_triplets(payload: str) -> list[tuple[float, float, float]]:
    """Split a comma-separated payload into (x, y, z) float triplets."""
    parts = [p.strip() for p in payload.split(",")]
    result = []
    for i in range(0, len(parts) - 2, 3):
        try:
            result.append((float(parts[i]), float(parts[i + 1]), float(parts[i + 2])))
        except (ValueError, IndexError) as exc:
            log.warning("Triplet parse error at index %d: %s", i, exc)
    return result


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    from database.db_context import DbContext
    from database.sensor_repository import SensorRepository

    log.info("Starting standalone MQTT subscriber")
    log.info("Broker : %s:%d", settings.mqtt_broker_host, settings.mqtt_broker_port)
    log.info("DB     : %s", settings.sqlite_db_path)
    log.info("Press Ctrl+C to stop")

    ctx  = DbContext(settings.sqlite_db_path)
    ctx.initialize()
    repo = SensorRepository(ctx)

    sub = SensorMqttSubscriber(
        broker_host=settings.mqtt_broker_host,
        broker_port=settings.mqtt_broker_port,
        repository=repo,
    )
    sub.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        sub.stop()
        ctx.close()
