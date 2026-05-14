"""
app/providers/deepseek.py
-------------------------
DeepSeek implementation of the LLMProvider interface.

DeepSeek exposes an OpenAI-compatible API at https://api.deepseek.com,
so this provider is a thin wrapper around the OpenAI SDK pointed at
the DeepSeek base URL. No separate SDK is needed.

Activated by setting in .env:
    LLM_PROVIDER=deepseek
    LLM_API_KEY=sk-your-deepseek-key
    LLM_MODEL=deepseek-chat        # or deepseek-reasoner

Get your API key at: https://platform.deepseek.com
"""

import json
from typing import Any, Callable

from app.logger import get_logger
from app.providers.base import LLMProvider

log = get_logger(__name__)

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_MAX_TURNS = 10
_LOOP_FALLBACK = (
    '{"action": "answer", "tts": "Sorry, I could not complete that request."}'
)


class DeepSeekProvider(LLMProvider):
    """
    LLM provider backed by the DeepSeek API (OpenAI-compatible format).

    Args:
        api_key:         DeepSeek API key (injected — never read from env here).
        model:           DeepSeek model identifier, e.g. "deepseek-chat".
        tool_dispatcher: Callable that executes a named tool and returns a string result.
                         Signature: (tool_name: str, tool_input: dict) -> str
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        tool_dispatcher: Callable[[str, dict[str, Any]], str],
    ) -> None:
        try:
            from openai import OpenAI

            # Point the OpenAI client at DeepSeek's compatible endpoint
            self._client = OpenAI(api_key=api_key, base_url=_DEEPSEEK_BASE_URL)
        except ImportError:
            log.error("openai package not installed. Run: uv add openai")
            raise

        self._model = model
        self._dispatch = tool_dispatcher
        log.debug(
            "DeepSeekProvider initialised (model=%s, base_url=%s)",
            model,
            _DEEPSEEK_BASE_URL,
        )

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Convert neutral tool definitions to OpenAI/DeepSeek function-calling format.

        Neutral format (Anthropic-style):
            {"name": "...", "description": "...", "input_schema": {...}}

        DeepSeek/OpenAI format:
            {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                },
            }
            for tool in tools
        ]

    def run(
        self,
        user_message: str,
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        """
        Execute the DeepSeek agentic loop for a single user turn.

        DeepSeek supports OpenAI-style tool_calls so the loop is identical
        to the OpenAI provider — build messages, handle tool_calls, repeat.
        """
        formatted_tools = self.format_tools(tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        for turn in range(1, _MAX_TURNS + 1):
            log.debug("DeepSeek API call — turn %d, messages=%d", turn, len(messages))

            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=formatted_tools,
                tool_choice="auto",
            )

            choice = response.choices[0]
            message = choice.message

            log.debug("DeepSeek response — finish_reason=%s", choice.finish_reason)

            if choice.finish_reason == "tool_calls" and message.tool_calls:
                messages.append(message)

                for tool_call in message.tool_calls:
                    tool_input = json.loads(tool_call.function.arguments)
                    log.info(
                        "Tool use requested: %s — inputs: %s",
                        tool_call.function.name,
                        tool_input,
                    )
                    result = self._dispatch(tool_call.function.name, tool_input)
                    log.debug("Tool result (%d chars): %s", len(result), result[:200])
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )

            else:
                text = message.content or "[no response]"
                log.debug("Final response (%d chars): %s", len(text), text[:200])
                return text

        log.error("Agent exceeded max turns (%d) — returning fallback", _MAX_TURNS)
        return _LOOP_FALLBACK
