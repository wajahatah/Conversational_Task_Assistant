"""
Ollama provider — local LLM inference via Ollama HTTP API.
Requires Ollama running at OLLAMA_BASE_URL with the selected model pulled.
Run: `ollama pull llama3.2` before using.
"""

from __future__ import annotations

import json

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm.base import BaseLLMProvider, LLMResponse, Message, ParsedTask, build_parsed_task
from app.llm.prompts import TASK_PARSE_SYSTEM, TASK_PARSE_USER


class OllamaProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)

    @property
    def provider_name(self) -> str:
        return "ollama"

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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def generate(
        self,
        prompt: str,
        history: list[Message] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        messages = self._build_messages(prompt, history, system_prompt)
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        return LLMResponse(
            text=data["message"]["content"],
            provider=self.provider_name,
            model=self._model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
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
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",  # Ollama JSON mode
        }
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        raw = data["message"]["content"]
        parsed = json.loads(raw)
        return build_parsed_task(parsed)

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/api/tags")
            return response.status_code == 200
        except Exception:
            return False
        except Exception:
            return False
