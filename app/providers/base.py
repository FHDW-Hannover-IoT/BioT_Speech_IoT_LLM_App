"""
app/providers/base.py
---------------------
Abstract base class defining the interface every LLM provider must implement.

Adding a new provider (e.g. Gemini, Mistral) means:
  1. Create app/providers/gemini.py
  2. Subclass LLMProvider and implement run()
  3. Register it in app/providers/__init__.py
  4. Set LLM_PROVIDER=gemini in .env

No other files need to change.
"""

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """
    Abstract interface for an LLM provider.

    Every concrete provider (Anthropic, OpenAI, etc.) must implement run().
    The agent calls only this interface — it never talks to a specific SDK directly.
    """

    @abstractmethod
    def run(
        self, user_message: str, tools: list[dict[str, Any]], system_prompt: str
    ) -> str:
        """
        Send a user message to the LLM and return the final text reply.

        Implementations must handle the full agentic loop internally:
        - Send the message with tool definitions
        - Execute any tool calls the model requests (via the dispatch callback)
        - Return the final plain-text response

        Args:
            user_message:  Raw text from the user or Android app.
            tools:         List of tool definitions in the provider's expected format.
            system_prompt: The system/instruction prompt for this session.

        Returns:
            The model's final plain-text reply as a string.
        """
        ...

    @abstractmethod
    def format_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Convert the provider-agnostic tool definitions into the format
        required by this specific provider's API.

        The agent stores tools in a neutral format. Each provider translates
        them into whatever structure its SDK expects.

        Args:
            tools: Provider-agnostic tool definitions.

        Returns:
            Tools formatted for this provider's API call.
        """
        ...
