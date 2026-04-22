"""
Unit tests for the LLM Provider Factory.
"""

import pytest
from unittest.mock import patch

from app.llm.factory import create_provider, get_llm_provider
from app.llm.openai_provider import OpenAIProvider
from app.llm.gemini_provider import GeminiProvider


def test_create_provider_openai():
    """Test factory creates OpenAI provider correctly."""
    with patch("app.config.settings.llm_provider", "openai"):
        provider = create_provider("openai")
        assert isinstance(provider, OpenAIProvider)
        assert provider.provider_name == "openai"


def test_create_provider_gemini():
    """Test factory creates Gemini provider correctly."""
    with patch("app.config.settings.llm_provider", "gemini"):
        provider = create_provider("gemini")
        assert isinstance(provider, GeminiProvider)
        assert provider.provider_name == "gemini"


def test_create_provider_invalid():
    """Test factory raises ValueError on unknown provider."""
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_provider("unknown_provider")


def test_get_llm_provider_singleton():
    """Test the cached singleton returns the same instance."""
    # Clear cache in case other tests ran it
    get_llm_provider.cache_clear()
    
    with patch("app.config.settings.llm_provider", "openai"):
        instance_1 = get_llm_provider()
        instance_2 = get_llm_provider()
        
        assert instance_1 is instance_2
