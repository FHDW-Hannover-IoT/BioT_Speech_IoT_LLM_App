"""
app/agent.py
------------
BioT Sensor Assistant — provider-agnostic agent orchestration.

Tool calls are dispatched to the MCP server over the MCP Streamable HTTP
transport using JSON-RPC 2.0 (POST to /mcp with a JSON-RPC envelope).
The agent is fully decoupled from the database — it only knows the MCP URL.
"""

import uuid as _uuid
from typing import Any

import httpx

from app.logger import get_logger
from app.providers import LLMProvider, create_provider
from config.settings import settings

log = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are BioT, an intelligent IoT assistant for the BioT Speech IoT project at FHDW Hannover.

The system connects an ESP8266 NodeMCU microcontroller to an Android app via MQTT.
Sensors: MPU-6050 accelerometer/gyroscope, A3144 hall effect sensor.
Voice input uses the Android phone's built-in microphone (Android SpeechRecognizer).

You have tools to query a live SQLite database of sensor readings. ALWAYS use them
to fetch real data — never invent or guess sensor values.

Database tables (mirror the Android Room database exactly):
  accel_data    — id, timestamp (ms), accelX (g),     accelY (g),     accelZ (g)
  gyro_data     — id, timestamp (ms), gyroX (deg/s),  gyroY (deg/s),  gyroZ (deg/s)
  magnet_data   — id, timestamp (ms), magnetX,        magnetY,        magnetZ
  ereignis_data — id, timestamp (ms), sensorType (ACCEL/GYRO/MAGNET), value, axis

MQTT topics:
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
ALWAYS spoken aloud regardless of action type.

If a tool call fails, or the user's intent is unclear, fall back to:

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

   Valid screen names (must match Android Activity class names exactly):
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
NAVIGATION vs DATA QUERY — CRITICAL DISTINCTION
────────────────────────────────────────────────────────────────────────────────

action=navigate is ONLY for pure navigation commands:
  "show gyro", "open the graph", "go to settings", "show events"
  → screen only, no data expected, tts says "Opening ..."

action=answer with tool use is for ALL data/value questions:
  "what is the magnetic value", "what are the current readings",
  "tell me the gyro X", "is there any accel data", "how high was the acceleration"
  → ALWAYS call a tool first, NEVER return action=navigate for these.

If the transcript mentions "value", "reading", "current", "latest", "how much",
"how high", "what is", "tell me" combined with a sensor name → action=answer + tool.

────────────────────────────────────────────────────────────────────────────────
HANDLING SPECIFIC USER INTENTS
────────────────────────────────────────────────────────────────────────────────

• "What is the [sensor] value?" / "Tell value (sensor) (axis?)"
  → call get_value_for_axis (if axis given) or get_latest_sensor_data.
  → Return action=answer. NEVER action=navigate for value questions.

• "Tell value mic" / "mic level" / any microphone query
  → The KY-037 mic hardware was removed. Respond:
    { "action": "answer", "tts": "The hardware microphone sensor is not part of this system. Voice input uses the phone's built-in mic." }

• "What mode is active?" / "Get mode"
  → The current mode is stored as a retained MQTT message, not in the database.
    Respond with action=answer and tell the user to check the mode label on the
    app's home screen.

• "Set epsilon …" / "Start calibration"
  → Not yet implemented. Navigate to SettingsActivity and explain in tts.

• "Create event for (sensor) (threshold)"
  → Navigate to EreignisActivity and tell the user to use the form there.

• Anomaly / spike questions
  → Call get_sensor_history, flag any value more than 2× the mean as a candidate.

• "Show last N minutes" / filter commands
  → Respond with action=apply_filter and minutes=N. Do NOT navigate away.

• Project documentation / requirements questions
  → ALWAYS call query_rag first.
→ If RAG has no answer, SAY YOU DON'T KNOW. NEVER GUESS!!!

• Any ambiguous intent
  → Pick the most likely action. Never guess sensor values — use tools.

Guidelines:
- ALWAYS use tools to fetch real data. Never invent values.
- Call get_db_schema first if unsure which columns a table has.
- If a table is empty, say so and suggest starting the Android app.
- Keep tts SHORT (under 20 seconds when spoken aloud).
- Always return valid JSON. Do not wrap in markdown fences.
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
            "Use for 'Tell value (sensor) (axis)' commands, e.g. "
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
            "Get sensor readings from the last N minutes with statistical summary "
            "(count, min, max, avg per axis). "
            "Use for: 'Show me the last 10 minutes of gyro data', "
            "'Were there any spikes?', anomaly detection, trend analysis."
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
        "name": "query_rag",
        "description": (
            "Query the project documentation vector store. "
            "Use for: architecture, requirements, glossary, or doc questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "User question to answer from docs.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many passages to use (default 6).",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_event_log",
        "description": (
            "Return recent threshold events from ereignis_data. "
            "Use for: 'Show notifications', 'How many events today?', "
            "'Were there any ACCEL events recently?', QUERY_RECENT_EVENTS commands."
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

    Tool calls are dispatched to the MCP server via JSON-RPC 2.0 over the
    Streamable HTTP transport (POST to /mcp).
    """

    def __init__(
        self,
        provider_name: str,
        api_key: str,
        model: str,
        mcp_server_url: str,
    ) -> None:
        self._mcp_url = mcp_server_url.rstrip("/")
        log.debug(
            "SensorAgent init (provider=%s, model=%s, mcp=%s)",
            provider_name,
            model,
            mcp_server_url,
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
        """
        Forward a tool call from the LLM to the MCP server.

        Uses JSON-RPC 2.0 over the MCP Streamable HTTP transport.
        The endpoint is the base /mcp path — NOT /tools/call (which does not
        exist in the FastMCP Streamable HTTP spec).
        """
        rpc_id = _uuid.uuid4().hex[:8]
        log.info(
            "MCP → tool=%s  inputs=%s  url=%s  id=%s",
            name,
            inputs,
            self._mcp_url,
            rpc_id,
        )

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": inputs,
            },
        }

        import time as _time

        t0 = _time.monotonic()
        try:
            response = httpx.post(
                self._mcp_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=settings.mcp_tool_timeout_secs,
            )
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            log.info(
                "MCP ← tool=%s  status=%d  ms=%d  id=%s",
                name,
                response.status_code,
                elapsed_ms,
                rpc_id,
            )
            response.raise_for_status()
            data = response.json()

            # JSON-RPC 2.0 wraps the tool result in {"result": {"content": [...]}}
            result_data = data.get("result", {})
            content = result_data.get("content", [])
            texts = [b["text"] for b in content if b.get("type") == "text"]
            result = "\n".join(texts) if texts else str(result_data)

            log.debug("MCP result (%d chars): %s", len(result), result[:300])
            return result

        except httpx.ConnectError:
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            msg = (
                f"MCP server unreachable at {self._mcp_url} (ms={elapsed_ms}). "
                "Ensure sensor_mcp_server.py is running."
            )
            log.error(
                "MCP ✗ ConnectError  tool=%s  url=%s  ms=%d",
                name,
                self._mcp_url,
                elapsed_ms,
            )
            return msg

        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            msg = (
                f"MCP server HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            )
            log.error(
                "MCP ✗ HTTPError  tool=%s  status=%d  ms=%d  body=%s",
                name,
                exc.response.status_code,
                elapsed_ms,
                exc.response.text[:200],
            )
            return msg

        except Exception as exc:
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            log.error(
                "MCP ✗ exception  tool=%s  ms=%d  err=%s",
                name,
                elapsed_ms,
                exc,
                exc_info=True,
            )
            return f"Tool dispatch error: {exc}"
