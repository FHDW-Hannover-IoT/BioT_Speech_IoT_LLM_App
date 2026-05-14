"""
app/providers/gemini.py
-----------------------
Google Gemini implementation of the LLMProvider interface.

Uses the official google-genai SDK (google-genai package).
Gemini supports function calling which maps to our tool system.

Activated by setting in .env:
    LLM_PROVIDER=gemini
    LLM_API_KEY=your-gemini-api-key
    LLM_MODEL=gemini-2.0-flash      # or gemini-1.5-pro

Get your API key at: https://aistudio.google.com/apikey

To install the required package:
    uv add google-genai
"""

from typing import Any, Callable

from app.logger import get_logger
from app.providers.base import LLMProvider

log = get_logger(__name__)

_MAX_TURNS = 10
_LOOP_FALLBACK = (
    '{"action": "answer", "tts": "Sorry, I could not complete that request."}'
)


class GeminiProvider(LLMProvider):
    """
    LLM provider backed by the Google Gemini API.

    Args:
        api_key:         Google Gemini API key (injected — never read from env here).
        model:           Gemini model identifier, e.g. "gemini-2.0-flash".
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
            from google import genai
            from google.genai import types

            self._genai = genai
            self._types = types
            self._client = genai.Client(api_key=api_key)
        except ImportError:
            log.error("google-genai package not installed. Run: uv add google-genai")
            raise

        self._model = model
        self._dispatch = tool_dispatcher
        log.debug("GeminiProvider initialised (model=%s)", model)

    def format_tools(self, tools: list[dict[str, Any]]) -> list[Any]:
        """
        Convert neutral tool definitions to Gemini FunctionDeclaration format.

        Neutral format (Anthropic-style):
            {"name": "...", "description": "...", "input_schema": {...}}

        Gemini format:
            genai.types.Tool with FunctionDeclaration objects
        """
        declarations = []
        for tool in tools:
            schema = tool.get("input_schema", {})
            # Gemini uses "parameters" with JSON Schema format
            declarations.append(
                self._types.FunctionDeclaration(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=self._types.Schema(
                        type=self._types.Type.OBJECT,
                        properties={
                            prop_name: self._types.Schema(
                                type=self._types.Type.STRING,
                                description=prop_val.get("description", ""),
                            )
                            for prop_name, prop_val in schema.get(
                                "properties", {}
                            ).items()
                        },
                        required=schema.get("required", []),
                    ),
                )
            )
        return [self._types.Tool(function_declarations=declarations)]

    def run(
        self,
        user_message: str,
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        """
        Execute the Gemini agentic loop for a single user turn.

        Sends message → handles function_call parts → feeds results back
        → repeats until Gemini returns a final text response.
        """
        formatted_tools = self.format_tools(tools)

        # Build the conversation history
        contents = [
            self._types.Content(
                role="user", parts=[self._types.Part(text=user_message)]
            )
        ]

        config = self._types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=formatted_tools,
        )

        for turn in range(1, _MAX_TURNS + 1):
            log.debug("Gemini API call — turn %d", turn)

            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )

            candidate = response.candidates[0]
            parts = candidate.content.parts

            log.debug(
                "Gemini response — finish_reason=%s, parts=%d",
                candidate.finish_reason,
                len(parts),
            )

            function_call_parts = [
                p for p in parts if hasattr(p, "function_call") and p.function_call
            ]

            if function_call_parts:
                contents.append(candidate.content)

                tool_response_parts = []
                for part in function_call_parts:
                    fc = part.function_call
                    tool_input = dict(fc.args)
                    log.info("Tool use requested: %s — inputs: %s", fc.name, tool_input)
                    result = self._dispatch(fc.name, tool_input)
                    log.debug("Tool result (%d chars): %s", len(result), result[:200])

                    tool_response_parts.append(
                        self._types.Part(
                            function_response=self._types.FunctionResponse(
                                name=fc.name,
                                response={"result": result},
                            )
                        )
                    )

                contents.append(
                    self._types.Content(
                        role="user",
                        parts=tool_response_parts,
                    )
                )

            else:
                text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
                text = " ".join(text_parts).strip() or "[no response]"
                log.debug("Final response (%d chars): %s", len(text), text[:200])
                return text

        log.error("Agent exceeded max turns (%d) — returning fallback", _MAX_TURNS)
        return _LOOP_FALLBACK
