"""
app/providers/__init__.py
-------------------------
Provider factory — resolves LLM_PROVIDER and returns the correct LLMProvider.

Supported providers:
    anthropic  — Claude (Anthropic)       https://console.anthropic.com
    openai     — GPT (OpenAI)             https://platform.openai.com
    deepseek   — DeepSeek (OpenAI-compat) https://platform.deepseek.com
    gemini     — Gemini (Google)          https://aistudio.google.com

Adding a new provider:
    1. Create app/providers/<name>.py implementing LLMProvider
    2. Add the name to _SUPPORTED below
    3. Add the import + return in create_provider()
    4. Add the default model in config/settings.py
    That's it — no other files change.
"""

from typing import Any, Callable

from app.logger import get_logger
from app.providers.base import LLMProvider

log = get_logger(__name__)

_SUPPORTED = ("anthropic", "openai", "deepseek", "gemini")


def create_provider(
    provider_name: str,
    api_key: str,
    model: str,
    tool_dispatcher: Callable[[str, dict[str, Any]], str],
) -> LLMProvider:
    """
    Factory — instantiates and returns the correct LLMProvider.

    Args:
        provider_name:   Value of LLM_PROVIDER in .env.
        api_key:         Provider API key (injected from settings).
        model:           Model identifier string.
        tool_dispatcher: Callable the provider uses to execute tool calls.

    Raises:
        ValueError: If provider_name is not in the supported registry.
        ImportError: If the required SDK for the provider is not installed.
    """
    name = provider_name.lower().strip()
    log.info("Creating LLM provider: %s (model=%s)", name, model)

    if name == "anthropic":
        from app.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=api_key, model=model, tool_dispatcher=tool_dispatcher
        )

    if name == "openai":
        from app.providers.openai import OpenAIProvider

        return OpenAIProvider(
            api_key=api_key, model=model, tool_dispatcher=tool_dispatcher
        )

    if name == "deepseek":
        from app.providers.deepseek import DeepSeekProvider

        return DeepSeekProvider(
            api_key=api_key, model=model, tool_dispatcher=tool_dispatcher
        )

    if name == "gemini":
        from app.providers.gemini import GeminiProvider

        return GeminiProvider(
            api_key=api_key, model=model, tool_dispatcher=tool_dispatcher
        )

    log.error(
        "Unsupported LLM provider: %r (supported: %s)",
        provider_name,
        ", ".join(_SUPPORTED),
    )
    raise ValueError(
        f"Unknown LLM provider: {provider_name!r}. "
        f"Supported providers: {', '.join(_SUPPORTED)}"
    )


__all__ = ["create_provider", "LLMProvider"]
