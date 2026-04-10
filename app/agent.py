"""
app/agent.py
------------
BioT Sensor Assistant — provider-agnostic agent orchestration.
"""

import sys
from pathlib import Path
from typing import Any

from app import database
from app.logger import get_logger
from app.providers import LLMProvider, create_provider

log = get_logger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are BioT, an intelligent IoT assistant for the BioT Speech IoT project at FHDW Hannover.

The system connects an ESP8266 NodeMCU microcontroller to an Android app via MQTT. \
Sensors include a KY-037 microphone, MPU-6050 accelerometer/gyroscope, and A3144 hall effect sensor.

You have access to tools that query a live SQLite database of sensor readings.

Database tables:
  accel_data    — id (int), timestamp (ms), accelX (g), accelY (g), accelZ (g)
  gyro_data     — id (int), timestamp (ms), gyroX (°/s), gyroY (°/s), gyroZ (°/s)
  magnet_data   — id (int), timestamp (ms), magnetX, magnetY, magnetZ
  ereignis_data — id (int), timestamp (ms), sensorType (text), value (float), axis (char)

MQTT topics:
  Sensor/Mic       — KY-037 sound level (0–1023 integer)
  Sensor/Bewegung  — accelerometer x,y,z
  Sensor/Gyro      — gyroscope x,y,z
  Sensor/Magnet    — hall sensor x,y,z
  Control/Mode     — STREAM / BURST / AVERAGE

When the user asks you to perform an app action (navigate, change mode, apply filter),
respond with a JSON object in this exact format:
  { "action": "navigate|mqtt_publish|apply_filter|clear_filter|answer",
    "tts": "Text to speak aloud",
    "screen": "ActivityName",       // only for action=navigate
    "topic": "Control/Mode",        // only for action=mqtt_publish
    "payload": "STREAM|BURST|AVERAGE", // only for action=mqtt_publish
    "minutes": 10 }                 // only for action=apply_filter

For pure data answers with no app action, use action="answer" and put the full answer in tts.

Guidelines:
- Always use tools to fetch real data. Never invent sensor values.
- Call get_db_schema first if you are unsure which columns a table has.
- For "latest" or "current" values, query ORDER BY timestamp DESC LIMIT 1.
- If a table is empty, say so clearly and suggest starting the Android app.
- Keep answers concise and practical.
"""

# ── Tool catalogue ────────────────────────────────────────────────────────────
_TOOLS: list[dict[str, Any]] = [
    {
        "name": "query_sensor_db",
        "description": (
            "Execute a read-only SQL SELECT query against the BioT sensor database. "
            "Use this to retrieve accelerometer, gyroscope, magnetometer, or event data. "
            "Always query real data — never guess values. "
            "Example: SELECT * FROM accel_data ORDER BY timestamp DESC LIMIT 10"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A valid SQLite SELECT statement. Only SELECT is permitted.",
                }
            },
            "required": ["sql"],
        },
    },
    {
        "name": "get_db_schema",
        "description": (
            "Return the CREATE TABLE statements for all tables in the sensor database. "
            "Call this when you need to know which columns a table has before writing a query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


class SensorAgent:
    """
    Stateless BioT sensor assistant.
    Orchestrates tool dispatch and delegates LLM communication to the injected provider.

    Args:
        provider_name: LLM provider identifier ("anthropic" or "openai").
        api_key:       Provider API key (injected from settings).
        model:         Model identifier string (injected from settings).
        db_path:       Path to the SQLite sensor database file.
    """

    def __init__(
        self,
        provider_name: str,
        api_key: str,
        model: str,
        db_path: Path,
    ) -> None:
        self._db_path = db_path
        log.debug("Initialising SensorAgent (provider=%s, model=%s, db=%s)", provider_name, model, db_path)

        self._provider: LLMProvider = create_provider(
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            tool_dispatcher=self._dispatch_tool,
        )
        log.info("SensorAgent ready")

    def run(self, user_message: str) -> str:
        """
        Process a single user message and return the assistant's final reply.

        Args:
            user_message: Raw text input from the user or Android app.

        Returns:
            The assistant's final text reply as a plain string.
        """
        log.debug("Agent.run called with: %r", user_message[:120])
        reply = self._provider.run(
            user_message=user_message,
            tools=_TOOLS,
            system_prompt=_SYSTEM_PROMPT,
        )
        log.debug("Agent.run reply: %r", reply[:120])
        return reply

    def _dispatch_tool(self, name: str, inputs: dict[str, Any]) -> str:
        """
        Route a tool call from the LLM to the correct implementation.
        Logs every tool call and its result length for debugging.
        """
        log.debug("Tool call: %s — inputs: %s", name, inputs)

        if name == "query_sensor_db":
            sql = inputs.get("sql", "")
            log.info("Executing SQL: %s", sql)
            result = database.query(self._db_path, sql)
            log.debug("SQL result (%d chars): %s", len(result), result[:200])
            return result

        if name == "get_db_schema":
            log.info("Fetching database schema")
            result = database.get_schema(self._db_path)
            log.debug("Schema result (%d chars)", len(result))
            return result

        log.warning("Unknown tool requested by LLM: %r", name)
        return f"Tool '{name}' is not implemented."