"""
LLM Provider Factory.

Single entry point for getting the active LLM provider.
Provider is selected via LLM_PROVIDER env variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from app.config import settings
from app.llm.base import BaseLLMProvider

ProviderName = Literal["openai", "gemini", "anthropic", "ollama", "huggingface"]


def create_provider(provider: ProviderName | None = None) -> BaseLLMProvider:
    """
    Instantiate and return the requested LLM provider.

    Args:
        provider: Provider name. Defaults to settings.llm_provider.

    Returns:
        A BaseLLMProvider instance ready for use.

    Raises:
        ValueError: if the provider name is not recognised.
        ImportError: if HuggingFace provider is selected but deps missing.
    """
    name = provider or settings.llm_provider

    if name == "openai":
        from app.llm.openai_provider import OpenAIProvider
        return OpenAIProvider()

    elif name == "gemini":
        from app.llm.gemini_provider import GeminiProvider
        return GeminiProvider()

    elif name == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider()

    elif name == "ollama":
        from app.llm.ollama_provider import OllamaProvider
        return OllamaProvider()

    elif name == "huggingface":
        from app.llm.huggingface_provider import HuggingFaceProvider
        return HuggingFaceProvider()

    else:
        raise ValueError(
            f"Unknown LLM provider: '{name}'. "
            "Valid options: openai | gemini | anthropic | ollama | huggingface"
        )


@lru_cache(maxsize=1)
def get_llm_provider() -> BaseLLMProvider:
    """
    Cached singleton LLM provider.
    Use this everywhere in the app — avoids repeated instantiation.

    To switch providers at runtime (e.g., in tests), call get_llm_provider.cache_clear()
    then call get_llm_provider() again.
    """
    return create_provider()
