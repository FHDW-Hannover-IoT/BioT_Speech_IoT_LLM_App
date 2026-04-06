"""
app/providers/__init__.py
-------------------------
Provider factory — the single place where LLM_PROVIDER is resolved.

Reads the LLM_PROVIDER setting and returns the correct LLMProvider subclass.
All other code imports only from this module, never from a specific provider file.

Usage:
    from app.providers import create_provider
    provider = create_provider(settings, tool_dispatcher)
"""

from typing import Any, Callable

from app.logger import get_logger
from app.providers.base import LLMProvider

log = get_logger(__name__)

_SUPPORTED = ("anthropic", "openai")


def create_provider(
    provider_name: str,
    api_key: str,
    model: str,
    tool_dispatcher: Callable[[str, dict[str, Any]], str],
) -> LLMProvider:
    """
    Factory — instantiates and returns the correct LLMProvider.

    Args:
        provider_name:   Value of LLM_PROVIDER ("anthropic" or "openai").
        api_key:         Provider API key (injected from settings).
        model:           Model identifier string.
        tool_dispatcher: Callable the provider uses to execute tool calls.
    """
    name = provider_name.lower().strip()
    log.info("Creating LLM provider: %s (model=%s)", name, model)

    if name == "anthropic":
        from app.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model, tool_dispatcher=tool_dispatcher)

    if name == "openai":
        from app.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model, tool_dispatcher=tool_dispatcher)

    log.error("Unsupported LLM provider: %r (supported: %s)", provider_name, ", ".join(_SUPPORTED))
    raise ValueError(
        f"Unknown LLM provider: {provider_name!r}. Supported: {', '.join(_SUPPORTED)}"
    )


__all__ = ["create_provider", "LLMProvider"]