"""
app/providers/anthropic.py
--------------------------
Anthropic Claude implementation of the LLMProvider interface.

Handles the full Claude tool-use agentic loop:
  1. Send user message + tools + system prompt to Claude
  2. If Claude requests a tool, execute it via the injected dispatch callback
  3. Feed the result back and repeat until Claude returns a final answer

Activated by setting LLM_PROVIDER=anthropic in .env
"""

import sys
from typing import Any, Callable

import anthropic

from app.providers.base import LLMProvider


class AnthropicProvider(LLMProvider):
    """
    LLM provider backed by the Anthropic Claude API.

    Args:
        api_key:           Anthropic API key (injected — never read from env here).
        model:             Claude model identifier, e.g. "claude-sonnet-4-6".
        tool_dispatcher:   Callable that executes a named tool and returns a string result.
                           Signature: (tool_name: str, tool_input: dict) -> str
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

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Anthropic's tool format matches our neutral format exactly, so no
        transformation is needed. Returned as-is.
        """
        return tools

    def run(
        self,
        user_message: str,
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        """
        Execute the Claude agentic loop for a single user turn.

        Sends the message, handles any tool_use stop reasons by dispatching
        tools and feeding results back, then returns the final text reply.
        """
        formatted_tools = self.format_tools(tools)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        while True:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                tools=formatted_tools,
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                # Append Claude's tool-request response to message history
                messages.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._dispatch(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Send tool results back so Claude can continue reasoning
                messages.append({"role": "user", "content": tool_results})

            else:
                # Final response — extract and return the text
                return self._extract_text(response.content)

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        """Extract plain text from a Claude response content block list."""
        texts = [block.text for block in content if hasattr(block, "text")]
        return " ".join(texts).strip() or "[no response]"