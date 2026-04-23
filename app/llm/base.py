"""
LLM Module — Abstract base provider interface.

ALL LLM providers must implement this interface.
The rule engine NEVER calls LLM directly — it goes through the Orchestrator,
which calls the active provider via this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:
    """A single turn in a conversation."""
    role: str     # "user" | "assistant" | "system"
    content: str


@dataclass
class LLMResponse:
    """Standardised response from any LLM provider."""
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ParsedTask:
    """Structured task extracted from natural language input."""
    title: str
    start_time: str        # ISO 8601 with timezone
    deadline: str          # ISO 8601 with timezone
    description: str | None = None
    confidence: float = 1.0


def build_parsed_task(data: dict) -> ParsedTask:
    """
    Build a ParsedTask from an LLM JSON response dict.

    Raises ValueError (not KeyError) when required fields are missing or null,
    so callers can distinguish parse failures from API/network errors.
    """
    missing = [f for f in ("title", "start_time", "deadline") if not data.get(f)]
    if missing:
        raise ValueError(f"LLM response missing required fields: {missing}")
    return ParsedTask(
        title=data["title"],
        start_time=data["start_time"],
        deadline=data["deadline"],
        description=data.get("description"),
    )


class BaseLLMProvider(ABC):
    """
    Abstract interface for all LLM providers.

    Provides two capabilities:
    1. generate()    — conversational response (HELP flows, messages)
    2. parse_task()  — extract structured task data from natural language
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name (e.g. 'openai', 'ollama')."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Active model identifier."""
        ...

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        history: list[Message] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """
        Generate a conversational response.

        Args:
            prompt:        The latest user message.
            history:       Previous turns in the conversation.
            system_prompt: Optional system-level instruction override.

        Returns:
            LLMResponse with the generated text.
        """
        ...

    @abstractmethod
    async def parse_task(
        self,
        user_message: str,
        user_timezone: str,
        current_date: str,
    ) -> ParsedTask:
        """
        Extract structured task data from a natural language message.

        Args:
            user_message:  Raw user input (e.g. "Finish report from 2pm to 6pm").
            user_timezone: IANA timezone string (e.g. "Asia/Karachi").
            current_date:  Today's date in YYYY-MM-DD format.

        Returns:
            ParsedTask with title, start_time, deadline, description.

        Raises:
            ValueError: if the message cannot be parsed into a valid task.
        """
        ...

    async def health_check(self) -> bool:
        """Verify the provider is reachable. Override if needed."""
        try:
            resp = await self.generate("ping", [])
            return bool(resp.text)
        except Exception:
            return False
