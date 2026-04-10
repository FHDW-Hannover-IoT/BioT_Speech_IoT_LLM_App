"""
config/settings.py
------------------
Centralised configuration for the BioT Sensor Assistant.

All settings are loaded from environment variables or the .env file.
No secrets are ever hardcoded here. The .env file is in .gitignore
and must NEVER be committed to version control.

Usage:
    from config.settings import settings
    provider = create_provider(settings.llm_provider, settings.llm_api_key, ...)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

_SUPPORTED_PROVIDERS = ("anthropic", "openai", "deepseek", "gemini")

# Default models per provider — used when LLM_MODEL is not set in .env
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai":    "gpt-4.1",
    "deepseek":  "deepseek-chat",
    "gemini":    "gemini-2.0-flash",
}


class Settings:
    """
    Immutable application settings resolved from environment variables.
    API keys are read once at startup and never logged or returned to callers.
    """

    @property
    def llm_provider(self) -> str:
        """
        Which LLM provider to use. Read from LLM_PROVIDER.
        Supported: anthropic, openai, deepseek, gemini.
        Defaults to anthropic.
        """
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
        """
        API key for the selected provider. Read from LLM_API_KEY.
        Raises SystemExit with a clear message if missing.
        """
        key = os.getenv("LLM_API_KEY", "").strip()
        if not key:
            print(
                "[config] ERROR: LLM_API_KEY is not set.\n"
                "         Add it to your .env file:\n"
                "         LLM_API_KEY=sk-ant-...   (Anthropic)\n"
                "         LLM_API_KEY=sk-...        (OpenAI)\n"
                "         LLM_API_KEY=sk-...        (DeepSeek)\n"
                "         LLM_API_KEY=AI...          (Gemini)",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return key

    @property
    def llm_model(self) -> str:
        """
        Model identifier. Read from LLM_MODEL.
        If not set, a sensible default is chosen per provider:
            anthropic → claude-sonnet-4-6
            openai    → gpt-4.1
            deepseek  → deepseek-chat
            gemini    → gemini-2.0-flash
        """
        model = os.getenv("LLM_MODEL", "").strip()
        if model:
            return model
        return _DEFAULT_MODELS.get(self.llm_provider, "claude-sonnet-4-6")

    @property
    def sqlite_db_path(self) -> Path:
        """Path to the sensor SQLite database. Defaults to data/sensor_database.db."""
        raw = os.getenv("SQLITE_DB_PATH", "").strip()
        path = Path(raw) if raw else Path(__file__).resolve().parent.parent / "data" / "sensor_database.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def server_host(self) -> str:
        return os.getenv("SERVER_HOST", "0.0.0.0").strip()

    @property
    def server_port(self) -> int:
        return int(os.getenv("SERVER_PORT", "8001"))

    def __repr__(self) -> str:
        # Never expose the API key in repr
        return (
            f"Settings(provider={self.llm_provider!r}, model={self.llm_model!r}, "
            f"db={self.sqlite_db_path}, host={self.server_host}, port={self.server_port})"
        )


settings = Settings()