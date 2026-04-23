"""Anthropic Claude provider."""

from __future__ import annotations

import json

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.base import BaseLLMProvider, LLMResponse, Message, ParsedTask, build_parsed_task
from app.llm.prompts import TASK_PARSE_SYSTEM, TASK_PARSE_USER

_MAX_TOKENS = 1024


class AnthropicProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    def _convert_history(self, history: list[Message] | None) -> list[dict]:
        """Convert Message list to Anthropic messages format."""
        return [
            {"role": m.role if m.role != "assistant" else "assistant", "content": m.content}
            for m in (history or [])
            if m.role != "system"
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def generate(
        self,
        prompt: str,
        history: list[Message] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        messages = self._convert_history(history)
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = await self._client.messages.create(**kwargs)
        text = response.content[0].text if response.content else ""
        return LLMResponse(
            text=text,
            provider=self.provider_name,
            model=self._model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def parse_task(
        self,
        user_message: str,
        user_timezone: str,
        current_date: str,
    ) -> ParsedTask:
        system = TASK_PARSE_SYSTEM.format(current_date=current_date)
        user_prompt = TASK_PARSE_USER.format(
            user_timezone=user_timezone,
            current_date=current_date,
            user_message=user_message,
        )
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text if response.content else "{}"
        # Strip potential markdown code fences
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        return build_parsed_task(data)
