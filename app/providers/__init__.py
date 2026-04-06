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

from app.providers.base import LLMProvider

# Registry of supported providers.
# To add a new provider: add its name here and import its class below.
_SUPPORTED = ("anthropic", "openai")


def create_provider(
    provider_name: str,
    api_key: str,
    model: str,
    tool_dispatcher: Callable[[str, dict[str, Any]], str],
) -> LLMProvider:
    """
    Factory function — instantiates and returns the correct LLMProvider.

    Args:
        provider_name:   Value of LLM_PROVIDER in .env ("anthropic" or "openai").
        api_key:         Provider API key (injected from settings).
        model:           Model identifier string (injected from settings).
        tool_dispatcher: Callable the provider uses to execute tool calls.

    Returns:
        A concrete LLMProvider instance ready to use.

    Raises:
        ValueError: If provider_name is not in the supported registry.
    """
    name = provider_name.lower().strip()

    if name == "anthropic":
        from app.providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            tool_dispatcher=tool_dispatcher,
        )

    if name == "openai":
        from app.providers.openai import OpenAIProvider
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            tool_dispatcher=tool_dispatcher,
        )

    raise ValueError(
        f"Unknown LLM provider: {provider_name!r}. "
        f"Supported providers: {', '.join(_SUPPORTED)}"
    )


__all__ = ["create_provider", "LLMProvider"]