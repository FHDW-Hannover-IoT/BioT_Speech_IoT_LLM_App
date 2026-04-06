"""
app/agent.py
------------
BioT Sensor Assistant — provider-agnostic agent orchestration.

Responsibilities:
- Define the system prompt and tool catalogue (provider-neutral format).
- Dispatch tool calls to the database layer.
- Delegate actual LLM communication to the injected LLMProvider.

This module has no direct dependency on any LLM SDK. Swapping providers
is done entirely in config — no changes here are ever needed.
"""

import sys
from pathlib import Path
from typing import Any

from app import database
from app.providers import LLMProvider, create_provider

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

Guidelines:
- Always use tools to fetch real data. Never invent sensor values.
- Call get_db_schema first if you are unsure which columns a table has.
- For "latest" or "current" values, query ORDER BY timestamp DESC LIMIT 1.
- If a table is empty, say so clearly and suggest starting the Android app.
- Keep answers concise and practical. Plain language — not every user is a developer.
"""

# ── Tool catalogue (provider-neutral format) ──────────────────────────────────
# Stored in Anthropic-style format. Each provider's format_tools() method
# translates this into whatever its own API expects.
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
                    "description": (
                        "A valid SQLite SELECT statement. "
                        "Only SELECT is permitted — write operations are blocked."
                    ),
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

    Orchestrates tool dispatch and delegates LLM communication to the
    injected provider. Has no knowledge of which LLM is being used.

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

        # Provider is created via the factory with this agent's tool dispatcher
        # injected so it can execute tool calls during the agentic loop
        self._provider: LLMProvider = create_provider(
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            tool_dispatcher=self._dispatch_tool,
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def run(self, user_message: str) -> str:
        """
        Process a single user message and return the assistant's final reply.

        Delegates the full agentic loop (including tool calls) to the provider.

        Args:
            user_message: Raw text input from the user or Android app.

        Returns:
            The assistant's final text reply as a plain string.
        """
        return self._provider.run(
            user_message=user_message,
            tools=_TOOLS,
            system_prompt=_SYSTEM_PROMPT,
        )

    # ── Private tool dispatcher ───────────────────────────────────────────────

    def _dispatch_tool(self, name: str, inputs: dict[str, Any]) -> str:
        """
        Route a tool call from the LLM to the correct implementation.

        This method is injected into the provider at construction time so the
        provider can call it during its agentic loop without knowing anything
        about the database layer.

        Args:
            name:   Tool name as requested by the LLM.
            inputs: Tool arguments as a dictionary.

        Returns:
            Tool result as a plain string to be fed back to the LLM.
        """
        if name == "query_sensor_db":
            return database.query(self._db_path, inputs.get("sql", ""))

        if name == "get_db_schema":
            return database.get_schema(self._db_path)

        print(f"[agent] WARNING: unknown tool requested: {name!r}", file=sys.stderr)
        return f"Tool '{name}' is not implemented."