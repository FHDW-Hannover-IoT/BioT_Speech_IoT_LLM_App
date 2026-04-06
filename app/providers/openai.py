"""
app/providers/openai.py
-----------------------
OpenAI GPT implementation of the LLMProvider interface.

Activated by setting LLM_PROVIDER=openai in .env

To activate:
  1. Run: uv add openai
  2. Set in .env:
       LLM_PROVIDER=openai
       LLM_API_KEY=sk-...
       LLM_MODEL=gpt-4.1
  3. Restart the server — no other code changes needed.
"""

import json
import sys
from typing import Any, Callable

from app.providers.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """
    LLM provider backed by the OpenAI API.

    Args:
        api_key:         OpenAI API key (injected — never read from env here).
        model:           OpenAI model identifier, e.g. "gpt-4.1".
        tool_dispatcher: Callable that executes a named tool and returns a string result.
                         Signature: (tool_name: str, tool_input: dict) -> str
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        tool_dispatcher: Callable[[str, dict[str, Any]], str],
    ) -> None:
        # Import lazily so the openai package is only required when this
        # provider is actually selected — keeps startup clean if not installed
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
        except ImportError:
            print(
                "[provider] ERROR: openai package not installed.\n"
                "           Run: uv add openai",
                file=sys.stderr,
            )
            raise

        self._model = model
        self._dispatch = tool_dispatcher

    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Convert neutral tool definitions to OpenAI's function-calling format.

        Neutral format (Anthropic-style):
            {"name": "...", "description": "...", "input_schema": {...}}

        OpenAI format:
            {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
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
        Execute the OpenAI agentic loop for a single user turn.

        Sends the message, handles any tool_calls responses by dispatching
        tools and feeding results back, then returns the final text reply.
        """
        formatted_tools = self.format_tools(tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        while True:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=formatted_tools,
                tool_choice="auto",
            )

            choice = response.choices[0]
            message = choice.message

            if choice.finish_reason == "tool_calls" and message.tool_calls:
                # Append the assistant's tool-request message
                messages.append(message)

                # Execute each tool call and append results
                for tool_call in message.tool_calls:
                    tool_input = json.loads(tool_call.function.arguments)
                    result = self._dispatch(tool_call.function.name, tool_input)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

            else:
                # Final response
                return message.content or "[no response]"