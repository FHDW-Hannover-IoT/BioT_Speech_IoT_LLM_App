"""
config/settings.py
------------------
Centralised configuration for the BioT Sensor Assistant.
All settings are loaded from .env. No secrets are hardcoded here.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

_SUPPORTED_PROVIDERS = ("anthropic", "openai", "deepseek", "gemini")
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai":    "gpt-4.1",
    "deepseek":  "deepseek-chat",
    "gemini":    "gemini-2.0-flash",
}


class Settings:
    """Immutable application settings resolved from environment variables."""

    # ── LLM ──────────────────────────────────────────────────────────────────

    @property
    def llm_provider(self) -> str:
        value = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        if value not in _SUPPORTED_PROVIDERS:
            print(
                f"[config] ERROR: LLM_PROVIDER={value!r} is not supported.\n"
                f"         Supported: {', '.join(_SUPPORTED_PROVIDERS)}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return value

    @property
    def llm_api_key(self) -> str:
        key = os.getenv("LLM_API_KEY", "").strip()
        if not key:
            print(
                "[config] ERROR: LLM_API_KEY is not set.\n"
                "         Add it to your .env file.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return key

    @property
    def llm_model(self) -> str:
        model = os.getenv("LLM_MODEL", "").strip()
        return model if model else _DEFAULT_MODELS.get(self.llm_provider, "claude-sonnet-4-6")

    # ── Database ──────────────────────────────────────────────────────────────

    @property
    def sqlite_db_path(self) -> Path:
        raw = os.getenv("SQLITE_DB_PATH", "").strip()
        path = Path(raw) if raw else Path(__file__).resolve().parent.parent / "data" / "sensor_database.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # ── MCP Server ────────────────────────────────────────────────────────────

    @property
    def mcp_server_port(self) -> int:
        """Port the MCP server listens on. Read from MCP_SERVER_PORT."""
        return int(os.getenv("MCP_SERVER_PORT", "8002"))

    @property
    def mcp_server_url(self) -> str:
        """
        Full URL the agent uses to call the MCP server.
        Read from MCP_SERVER_URL.
        Defaults to http://localhost:{mcp_server_port}/mcp.

        To deploy publicly, set MCP_SERVER_URL to the public address:
            MCP_SERVER_URL=http://your-server.com:8002/mcp
        """
        return os.getenv(
            "MCP_SERVER_URL",
            f"http://localhost:{self.mcp_server_port}/mcp",
        ).strip()

    # ── MQTT Broker ───────────────────────────────────────────────────────────

    @property
    def mqtt_broker_host(self) -> str:
        """Mosquitto broker host. Defaults to 127.0.0.1 (same PC as server)."""
        return os.getenv("MQTT_BROKER_HOST", "127.0.0.1").strip()

    @property
    def mqtt_broker_port(self) -> int:
        """Mosquitto broker port. Defaults to 1883."""
        return int(os.getenv("MQTT_BROKER_PORT", "1883"))

    # ── FastAPI Server ────────────────────────────────────────────────────────

    @property
    def server_host(self) -> str:
        return os.getenv("SERVER_HOST", "0.0.0.0").strip()

    @property
    def server_port(self) -> int:
        return int(os.getenv("SERVER_PORT", "8001"))

    # ── Misc ──────────────────────────────────────────────────────────────────

    @property
    def mcp_fs_roots(self) -> str:
        return os.getenv("MCP_FS_ROOTS", "").strip()

    def __repr__(self) -> str:
        return (
            f"Settings(provider={self.llm_provider!r}, model={self.llm_model!r}, "
            f"db={self.sqlite_db_path}, "
            f"mcp={self.mcp_server_url}, "
            f"mqtt={self.mqtt_broker_host}:{self.mqtt_broker_port}, "
            f"server={self.server_host}:{self.server_port})"
        )


settings = Settings()