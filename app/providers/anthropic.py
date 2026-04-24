"""
app/providers/anthropic.py
--------------------------
Anthropic Claude implementation of the LLMProvider interface.
"""

import sys
from typing import Any, Callable

import anthropic

from app.logger import get_logger
from app.providers.base import LLMProvider

log = get_logger(__name__)

_MAX_TURNS = 10
_LOOP_FALLBACK = '{"action": "answer", "tts": "Sorry, I could not complete that request."}'


class AnthropicProvider(LLMProvider):
    """
    LLM provider backed by the Anthropic Claude API.

    Args:
        api_key:         Anthropic API key (injected — never read from env here).
        model:           Claude model identifier.
        tool_dispatcher: Callable that executes a named tool and returns a string result.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        tool_dispatcher: Callable[[str, dict[str, Any]], str],
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._dispatch = tool_dispatcher
        log.debug("AnthropicProvider initialised (model=%s)", model)

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic's tool format matches our neutral format exactly."""
        return tools

    def run(
        self,
        user_message: str,
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        """Execute the Claude agentic loop for a single user turn."""
        formatted_tools = self.format_tools(tools)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        for turn in range(1, _MAX_TURNS + 1):
            log.debug("Anthropic API call — turn %d, messages=%d", turn, len(messages))

            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=system_prompt,
                    tools=formatted_tools,
                    messages=messages,
                )
            except anthropic.AuthenticationError as exc:
                log.error("Anthropic authentication failed: %s", exc)
                raise
            except anthropic.APIConnectionError as exc:
                log.error("Anthropic connection error: %s", exc)
                raise
            except anthropic.RateLimitError as exc:
                log.warning("Anthropic rate limit hit: %s", exc)
                raise
            except anthropic.APIStatusError as exc:
                log.error("Anthropic API error (status=%s): %s", exc.status_code, exc)
                raise

            log.debug("Anthropic response — stop_reason=%s, content_blocks=%d", response.stop_reason, len(response.content))

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        log.info("Tool use requested: %s — inputs: %s", block.name, block.input)
                        result = self._dispatch(block.name, block.input)
                        log.debug("Tool result (%d chars): %s", len(result), result[:200])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})

            else:
                text = self._extract_text(response.content)
                log.debug("Final response (%d chars): %s", len(text), text[:200])
                return text

        log.error("Agent exceeded max turns (%d) — returning fallback", _MAX_TURNS)
        return _LOOP_FALLBACK

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        texts = [block.text for block in content if hasattr(block, "text")]
        return " ".join(texts).strip() or "[no response]"