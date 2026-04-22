"""
HuggingFace local inference provider.

Uses the `transformers` pipeline for text generation.
NOTE: Install separately — not in default requirements.txt to keep
      the base install lightweight:

  pip install transformers torch accelerate

Set HF_DEVICE=cuda for GPU, mps for Apple Silicon, cpu for CPU.

Generation is configured to avoid the "looping content" error that
transformers raises when the model starts repeating itself.  The key
knobs are:
  - repetition_penalty > 1.0   penalises repeated n-grams
  - do_sample=True             enables stochastic sampling
  - temperature / top_p        control diversity
  - return_full_text=False     return only the new tokens, not the prompt
"""

from __future__ import annotations

import json
import asyncio
from functools import partial

from app.config import settings
from app.llm.base import BaseLLMProvider, LLMResponse, Message, ParsedTask
from app.llm.prompts import TASK_PARSE_SYSTEM, TASK_PARSE_USER

# ---------------------------------------------------------------------------
# Default generation kwargs — tuned to suppress repetition loops.
# Override any of these via matching HF_* env vars in settings if desired.
# ---------------------------------------------------------------------------
_DEFAULT_GEN_KWARGS: dict = {
    "do_sample": True,
    "temperature": 0.7,
    "top_p": 0.9,
    "repetition_penalty": 1.15,   # > 1.0 penalises repeated token sequences
    "return_full_text": False,    # only return the newly generated tokens
}


class HuggingFaceProvider(BaseLLMProvider):
    def __init__(self) -> None:
        # Lazy import — only required if HuggingFace provider is selected
        try:
            from transformers import pipeline
        except ImportError as e:
            raise ImportError(
                "HuggingFace provider requires 'transformers' and 'torch'. "
                "Install with: pip install transformers torch accelerate"
            ) from e

        self._model_name = settings.hf_model_name
        self._device = settings.hf_device

        # Build generation kwargs, allowing optional settings overrides
        self._gen_kwargs = dict(_DEFAULT_GEN_KWARGS)
        if hasattr(settings, "hf_temperature") and settings.hf_temperature is not None:
            self._gen_kwargs["temperature"] = settings.hf_temperature
        if hasattr(settings, "hf_repetition_penalty") and settings.hf_repetition_penalty is not None:
            self._gen_kwargs["repetition_penalty"] = settings.hf_repetition_penalty

        self._pipe = pipeline(
            "text-generation",
            model=self._model_name,
            device=self._device,
            trust_remote_code=True,
        )

    @property
    def provider_name(self) -> str:
        return "huggingface"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _build_prompt(
        self,
        prompt: str,
        history: list[Message] | None,
        system_prompt: str | None,
    ) -> list[dict]:
        """Build a messages list compatible with chat templates."""
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for m in (history or []):
            msgs.append({"role": m.role, "content": m.content})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _run_sync(self, messages: list[dict], max_new_tokens: int = 512) -> str:
        """Run the pipeline synchronously (called in a thread pool)."""
        kwargs = {**self._gen_kwargs, "max_new_tokens": max_new_tokens}
        output = self._pipe(messages, **kwargs)

        generated = output[0]["generated_text"]

        # When return_full_text=False, generated_text is a plain string
        if isinstance(generated, str):
            return generated.strip()

        # When return_full_text=True (fallback), it's a list of messages;
        # the last entry is the assistant's reply
        if isinstance(generated, list):
            return generated[-1].get("content", "").strip()

        return str(generated).strip()

    async def generate(
        self,
        prompt: str,
        history: list[Message] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        messages = self._build_prompt(prompt, history, system_prompt)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, partial(self._run_sync, messages))
        return LLMResponse(
            text=text,
            provider=self.provider_name,
            model=self._model_name,
        )

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
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, partial(self._run_sync, messages, 256)
        )
        # Strip markdown code fences if present
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        # Extract JSON from response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"Could not extract JSON from HuggingFace response: {raw}")
        data = json.loads(raw[start:end])
        return ParsedTask(
            title=data["title"],
            start_time=data["start_time"],
            deadline=data["deadline"],
            description=data.get("description"),
        )
