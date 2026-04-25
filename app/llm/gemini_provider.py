"""Google Gemini provider."""

from __future__ import annotations

import json

import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.base import BaseLLMProvider, LLMResponse, Message, ParsedTask, build_parsed_task
from app.llm.prompts import TASK_PARSE_SYSTEM, TASK_PARSE_USER


class GeminiProvider(BaseLLMProvider):
    def __init__(self) -> None:
        genai.configure(api_key=settings.gemini_api_key)
        self._model_name = settings.gemini_model
        self._model = genai.GenerativeModel(self._model_name)

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _build_history(self, history: list[Message] | None) -> list[dict]:
        """Convert Message list to Gemini's content format."""
        gemini_history = []
        for m in (history or []):
            role = "model" if m.role == "assistant" else "user"
            gemini_history.append({"role": role, "parts": [m.content]})
        return gemini_history

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def generate(
        self,
        prompt: str,
        history: list[Message] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        # Gemini handles system prompt via system_instruction
        model = genai.GenerativeModel(
            self._model_name,
            system_instruction=system_prompt or "",
        )
        chat = model.start_chat(history=self._build_history(history))
        response = await chat.send_message_async(prompt)
        return LLMResponse(
            text=response.text,
            provider=self.provider_name,
            model=self._model_name,
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
        model = genai.GenerativeModel(
            self._model_name,
            system_instruction=system,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        response = await model.generate_content_async(user_prompt)
        data = json.loads(response.text)
        return build_parsed_task(data)
        data = json.loads(response.text)
        return build_parsed_task(data)
