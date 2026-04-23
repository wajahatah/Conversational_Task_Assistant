"""OpenAI provider — GPT-4o, GPT-4-turbo, GPT-3.5-turbo, etc."""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.base import BaseLLMProvider, LLMResponse, Message, ParsedTask, build_parsed_task
from app.llm.prompts import TASK_PARSE_SYSTEM, TASK_PARSE_USER


class OpenAIProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    def _build_messages(
        self,
        prompt: str,
        history: list[Message] | None,
        system_prompt: str | None,
    ) -> list[dict]:
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for m in (history or []):
            msgs.append({"role": m.role, "content": m.content})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def generate(
        self,
        prompt: str,
        history: list[Message] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        messages = self._build_messages(prompt, history, system_prompt)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=settings.openai_max_tokens,
            temperature=settings.openai_temperature,
        )
        choice = response.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            provider=self.provider_name,
            model=self._model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
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
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,  # low temp for structured extraction
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        return build_parsed_task(data)
