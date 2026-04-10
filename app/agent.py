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

You have tools to query a live SQLite database of sensor readings. Always use them
to fetch real data — never invent or guess sensor values.

Database tables:
  accel_data    — timestamp (ms), accelX (g), accelY (g), accelZ (g)
  gyro_data     — timestamp (ms), gyroX (deg/s), gyroY (deg/s), gyroZ (deg/s)
  magnet_data   — timestamp (ms), magnetX, magnetY, magnetZ
  ereignis_data — timestamp (ms), sensorType (ACCEL/GYRO/MAGNET), value, axis

MQTT topics:
  Sensor/Mic       — KY-037 sound level (0-1023, display-only, not in DB)
  Sensor/Bewegung  — accelerometer x,y,z -> accel_data
  Sensor/Gyro      — gyroscope x,y,z -> gyro_data
  Sensor/Magnet    — hall sensor x,y,z -> magnet_data
  Control/Mode     — STREAM / BURST / AVERAGE

When the user asks you to perform an app action (navigate, change mode, apply filter),
respond ONLY with a JSON object in this exact format:

  { "action": "navigate|mqtt_publish|apply_filter|clear_filter|answer",
    "tts": "Text to speak aloud via Android TTS",
    "screen": "ActivityName",              (only for action=navigate)
    "topic": "Control/Mode",              (only for action=mqtt_publish)
    "payload": "STREAM|BURST|AVERAGE",    (only for action=mqtt_publish)
    "minutes": 10 }                       (only for action=apply_filter)

Screen names: MainActivity, AccelActivity, GyroActivity, MagnetActivity,
              MainGraphActivity, EreignisActivity, SettingsActivity

For pure data answers with no app action:
  { "action": "answer", "tts": "Your answer here" }

Guidelines:
- Always use tools to fetch real data. Never guess.
- Call get_db_schema first if unsure which columns a table has.
- If a table is empty, say so and suggest starting the Android app.
- Keep answers concise and always include a tts field.
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