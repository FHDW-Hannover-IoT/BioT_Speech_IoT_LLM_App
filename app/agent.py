"""
app/agent.py
------------
BioT Sensor Assistant — provider-agnostic agent orchestration.

Tool calls are dispatched to the MCP server over HTTP (streamable-http).
The agent is fully decoupled from the database — it only knows the MCP URL.
"""

from pathlib import Path
from typing import Any

import httpx

from app.logger import get_logger
from app.providers import LLMProvider, create_provider

log = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are BioT, an intelligent IoT assistant for the BioT Speech IoT project at FHDW Hannover.

The system connects an ESP8266 NodeMCU microcontroller to an Android app via MQTT.
Sensors: KY-037 microphone, MPU-6050 accelerometer/gyroscope, A3144 hall effect sensor.

You have tools to query a live SQLite database of sensor readings. ALWAYS use them
to fetch real data — never invent or guess sensor values.

Database tables (mirror the Android Room database exactly):
  accel_data    — id, timestamp (ms), accelX (g),     accelY (g),     accelZ (g)
  gyro_data     — id, timestamp (ms), gyroX (deg/s),  gyroY (deg/s),  gyroZ (deg/s)
  magnet_data   — id, timestamp (ms), magnetX,        magnetY,        magnetZ
  ereignis_data — id, timestamp (ms), sensorType (ACCEL/GYRO/MAGNET), value, axis

MQTT topics:
  Sensor/Mic              — KY-037 sound level (0-1023, display-only, not in DB)
  Sensor/Bewegung         — accelerometer x,y,z   → accel_data
  Sensor/Gyro             — gyroscope x,y,z       → gyro_data
  Sensor/Magnet           — hall sensor x,y,z     → magnet_data
  Control/Mode            — STREAM | BURST | AVERAGE
                            (transmission cadence — how often the ESP8266 publishes)
  Control/OperatingMode   — AUTARK | SUPERVISION | EVENT | IDENTIFICATION
                            (operating mode — what the ESP8266 is doing)

Operating modes (from BioT_Speech_IoT_Doc/doc/content/command-dictionary.adoc):
  AUTARK         — sensors stop sending data (power saving)
  SUPERVISION    — sensors send all data; app shows homescreen
  EVENT          — sensors only send data when thresholds are crossed; app notifies
  IDENTIFICATION — sensors send all data; app forwards to the database

────────────────────────────────────────────────────────────────────────────────
RESPONSE FORMAT — IMPORTANT
────────────────────────────────────────────────────────────────────────────────

Every reply MUST be a single JSON object matching one of the action types below.
The Android app parses the JSON and dispatches the action; the `tts` field is
ALWAYS spoken aloud regardless of action.

If the Android app sends a question that cannot be parsed into any action, OR
if a tool call fails, OR if the user's transcript is unclear (UNKNOWN_INTENT),
fall back to:

  { "action": "answer",
    "tts": "Sorry, I didn't catch that. Could you repeat your question?" }

Action types
────────────

1. Pure data answer (no app side-effect):
     { "action": "answer",
       "tts": "The latest gyro reading is X 0.12, Y -0.45, Z 9.81." }

2. Navigate the Android app to a screen:
     { "action": "navigate",
       "screen": "GyroActivity",
       "tts": "Opening Gyroscope" }

   Valid screen names (must match Android Activity class names):
     MainActivity, AccelActivity, GyroActivity, MagnetActivity,
     MainGraphActivity, EreignisActivity, SettingsActivity

3. Publish an MQTT control message:
     { "action": "mqtt_publish",
       "topic": "Control/Mode",
       "payload": "BURST",
       "tts": "Burst mode active" }

   Valid (topic, payload) pairs:
     ("Control/Mode",          "STREAM" | "BURST" | "AVERAGE")
     ("Control/OperatingMode", "AUTARK" | "SUPERVISION" | "EVENT" | "IDENTIFICATION")

4. Apply a time filter on the currently visible chart:
     { "action": "apply_filter",
       "minutes": 10,
       "tts": "Showing the last ten minutes" }

5. Clear all active filters:
     { "action": "clear_filter",
       "tts": "Filter cleared" }

────────────────────────────────────────────────────────────────────────────────
HANDLING SPECIFIC USER INTENTS
────────────────────────────────────────────────────────────────────────────────

• "Tell me the value of (sensor) (axis?)"  →  call get_latest_sensor_data or
  get_value_for_axis, then respond with action=answer and the value(s) in tts.

• "What mode is active?" / "Get mode"  →  there's no DB table for current mode.
  Respond with action=answer and ask the user to look at the mode label on the
  app's home screen.

• "Set epsilon …" / "Start calibration"  →  these features are not yet
  implemented in the Android app. Respond with action=answer and tell the user
  it's not available, suggest opening Settings.

• "Create event for (sensor) (threshold)"  →  navigate to EreignisActivity and
  tell the user to use the form there. The Android app does not yet accept
  programmatic event creation from the LLM.

• Anomaly / spike questions  →  call get_sensor_history, eyeball the values,
  flag anything more than 2× the mean as a candidate spike.

• Anything ambiguous  →  pick the most likely action; never guess sensor values.

Guidelines:
- ALWAYS use tools to fetch real data. Never guess.
- Call get_db_schema first if you're unsure which columns a table has.
- If a table is empty, say so and suggest starting the Android app.
- Keep the tts field SHORT (under 20 seconds when spoken).
- Always return valid JSON. Do not wrap it in markdown fences.
"""

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_latest_sensor_data",
        "description": (
            "Get the single most recent reading for a sensor. "
            "Use for: 'What is the current accelerometer value?', "
            "'Was ist der aktuelle Gyro-Wert?', 'Show me the latest reading'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor": {
                    "type": "string",
                    "description": (
                        "Sensor name. Accepts: accel, gyro, magnet, bewegung, "
                        "beschleunigung, gyroskop, magnetfeld, hall."
                    ),
                }
            },
            "required": ["sensor"],
        },
    },
    {
        "name": "get_value_for_axis",
        "description": (
            "Get the latest value for a single axis (X, Y, or Z) of a sensor. "
            "Use for the .adoc 'Tell value (sensor) (axis)' command, e.g. "
            "'tell me the gyro X value', 'sage mir die Beschleunigung Z'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor": {
                    "type": "string",
                    "description": "Sensor name (accel, gyro, magnet — German or English).",
                },
                "axis": {
                    "type": "string",
                    "description": "Axis: x, y, or z (case-insensitive).",
                },
            },
            "required": ["sensor", "axis"],
        },
    },
    {
        "name": "get_sensor_history",
        "description": (
            "Get sensor readings from the last N minutes with statistical summary. "
            "Use for: 'Show me the last 10 minutes of gyro data', "
            "'Were there any spikes?', 'Zeige die Daten der letzten Stunde'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor": {
                    "type": "string",
                    "description": "Sensor name (accel, gyro, magnet — German or English).",
                },
                "minutes": {
                    "type": "integer",
                    "description": "How many minutes back to fetch. Default 10.",
                },
            },
            "required": ["sensor"],
        },
    },
    {
        "name": "get_db_schema",
        "description": (
            "Return CREATE TABLE statements for all tables. "
            "Call this when unsure which columns a table has. "
            "Use for: 'What tables are in the database?', 'Welche Daten werden gespeichert?'"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_event_log",
        "description": (
            "Return recent threshold events from ereignis_data. "
            "Use for: 'How many events today?', 'Zeige die letzten Ereignisse', "
            "'Were there any ACCEL events recently?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum events to return. Default 10.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_row_count",
        "description": (
            "Return row counts to check data availability. "
            "Use for: 'Is there any data?', 'How many readings?', "
            "'Wie viele Eintraege gibt es?', 'Gibt es Gyro-Daten?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": (
                        "Table name (accel_data, gyro_data, magnet_data, ereignis_data) "
                        "or 'all' for all tables."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "execute_query",
        "description": (
            "Execute a raw read-only SQL SELECT for complex queries not covered "
            "by the other tools. Only SELECT permitted. "
            "Use for custom aggregations, trend analysis, date range queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A valid SQLite SELECT statement.",
                }
            },
            "required": ["sql"],
        },
    },
]


class SensorAgent:
    """
    Stateless BioT sensor assistant.

    Tool calls are dispatched to the MCP server over HTTP.
    The agent is fully decoupled from the database — only knows the MCP URL.
    When deploying publicly, only MCP_SERVER_URL in .env needs to change.
    """

    def __init__(
        self,
        provider_name: str,
        api_key: str,
        model: str,
        db_path: Path,
        mcp_server_url: str,
    ) -> None:
        self._mcp_url = mcp_server_url.rstrip("/")
        log.debug(
            "SensorAgent init (provider=%s, model=%s, mcp=%s)",
            provider_name, model, mcp_server_url,
        )
        self._provider: LLMProvider = create_provider(
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            tool_dispatcher=self._dispatch_tool,
        )
        log.info("SensorAgent ready")

    def run(self, user_message: str) -> str:
        log.debug("Agent.run: %r", user_message[:120])
        reply = self._provider.run(
            user_message=user_message,
            tools=_TOOLS,
            system_prompt=_SYSTEM_PROMPT,
        )
        log.debug("Agent.run reply: %r", reply[:120])
        return reply

    def _dispatch_tool(self, name: str, inputs: dict[str, Any]) -> str:
        """Forward a tool call from the LLM to the MCP server over HTTP."""
        log.info("Dispatching tool via MCP: %s — inputs: %s", name, inputs)

        try:
            response = httpx.post(
                f"{self._mcp_url}/tools/call",
                json={"name": name, "arguments": inputs},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            content = data.get("content", [])
            texts = [b["text"] for b in content if b.get("type") == "text"]
            result = "\n".join(texts) if texts else str(data)

            log.debug("MCP result (%d chars): %s", len(result), result[:200])
            return result

        except httpx.ConnectError:
            msg = (
                f"MCP server unreachable at {self._mcp_url}. "
                "Ensure sensor_mcp_server.py is running on port configured in .env."
            )
            log.error(msg)
            return msg

        except httpx.HTTPStatusError as exc:
            msg = f"MCP server HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            log.error("MCP HTTP error for tool %s: %s", name, msg)
            return msg

        except Exception as exc:
            log.error("Tool dispatch error %s: %s", name, exc, exc_info=True)
            return f"Tool dispatch error: {exc}"
