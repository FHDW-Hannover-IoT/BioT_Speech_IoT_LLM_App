"""
config/settings.py
------------------
Centralised configuration for the BioT Sensor Assistant.

All values are resolved from .env (or environment variables) exactly once
in __init__ and stored as plain attributes.  No @property re-evaluation on
every access, no repeated os.getenv() calls, no repeated validation.
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
    "openai": "gpt-4.1",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-2.0-flash",
}


class Settings:
    """
    Immutable application settings resolved once from environment variables.

    All attributes are set in __init__ — reading them is a plain attribute
    access with zero overhead and no side effects.
    """

    def __init__(self) -> None:

        # ── LLM ──────────────────────────────────────────────────────────────
        provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        if provider not in _SUPPORTED_PROVIDERS:
            print(
                f"[config] ERROR: LLM_PROVIDER={provider!r} is not supported.\n"
                f"         Supported: {', '.join(_SUPPORTED_PROVIDERS)}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        self.llm_provider: str = provider

        api_key = os.getenv("LLM_API_KEY", "").strip()
        if not api_key:
            print(
                "[config] ERROR: LLM_API_KEY is not set.\n"
                "         Add it to your .env file.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        self.llm_api_key: str = api_key

        model = os.getenv("LLM_MODEL", "").strip()
        self.llm_model: str = (
            model if model else _DEFAULT_MODELS.get(provider, "claude-sonnet-4-6")
        )

        # ── Database ──────────────────────────────────────────────────────────
        raw_db = os.getenv("SQLITE_DB_PATH", "").strip()
        db_path = (
            Path(raw_db)
            if raw_db
            else Path(__file__).resolve().parent.parent / "data" / "sensor_database.db"
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sqlite_db_path: Path = db_path

        # ── MCP Server ────────────────────────────────────────────────────────
        self.mcp_server_port: int = int(os.getenv("MCP_SERVER_PORT", "8002"))
        self.mcp_server_url: str = os.getenv(
            "MCP_SERVER_URL",
            f"http://localhost:{self.mcp_server_port}/mcp",
        ).strip()

        # MCP startup probe settings (replaces hardcoded magic numbers in main.py)
        self.mcp_probe_attempts: int = int(os.getenv("MCP_PROBE_ATTEMPTS", "15"))
        self.mcp_probe_interval_secs: float = float(
            os.getenv("MCP_PROBE_INTERVAL_SECS", "1.0")
        )
        self.mcp_probe_timeout_secs: float = float(
            os.getenv("MCP_PROBE_TIMEOUT_SECS", "2.0")
        )

        # MCP tool call HTTP timeout (agent → MCP server per tool call)
        self.mcp_tool_timeout_secs: float = float(
            os.getenv("MCP_TOOL_TIMEOUT_SECS", "30.0")
        )

        # Maximum rows returned by any MCP tool
        self.mcp_max_rows: int = int(os.getenv("MCP_MAX_ROWS", "50"))

        # Max rows per page for /data/accel|gyro|magnet (Android pagination).
        # Android sends ?limit=N per page; server caps at this value.
        self.data_fetch_page_size: int = int(os.getenv("DATA_FETCH_PAGE_SIZE", "500"))

        # ── MQTT Broker ───────────────────────────────────────────────────────
        self.mqtt_broker_host: str = os.getenv("MQTT_BROKER_HOST", "127.0.0.1").strip()
        self.mqtt_broker_port: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))

        # Seconds to wait before retrying a dropped MQTT connection
        self.mqtt_reconnect_delay_secs: int = int(
            os.getenv("MQTT_RECONNECT_DELAY_SECS", "5")
        )

        # Client ID prefix — a UUID suffix is appended at runtime to prevent
        # broker kick-off when two instances start simultaneously.
        self.mqtt_client_id_prefix: str = os.getenv(
            "MQTT_CLIENT_ID_PREFIX", "biot-llm-subscriber"
        ).strip()

        # ── FastAPI Server ────────────────────────────────────────────────────
        self.server_host: str = os.getenv("SERVER_HOST", "0.0.0.0").strip()
        self.server_port: int = int(os.getenv("SERVER_PORT", "8001"))

        # ── Seeding ───────────────────────────────────────────────────────────
        self.seed_on_startup: bool = os.getenv("SEED_ON_STARTUP", "true").strip().lower() not in ("false", "0", "no")
        self.seed_hours: int = int(os.getenv("SEED_HOURS", "24"))
        self.seed_random_seed: int = int(os.getenv("SEED_RANDOM_SEED", "42"))

        # ── Misc ──────────────────────────────────────────────────────────────
        self.mcp_fs_roots: str = os.getenv("MCP_FS_ROOTS", "").strip()

    def __repr__(self) -> str:
        return (
            f"Settings(provider={self.llm_provider!r}, model={self.llm_model!r}, "
            f"db={self.sqlite_db_path}, "
            f"mcp={self.mcp_server_url}, "
            f"mqtt={self.mqtt_broker_host}:{self.mqtt_broker_port}, "
            f"server={self.server_host}:{self.server_port})"
        )


settings = Settings()
