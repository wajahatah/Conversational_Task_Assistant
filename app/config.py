from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_env: Literal["development", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True
    secret_key: str = "change-this-to-a-random-secret-string"

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "task_user"
    postgres_password: str = "task_password"
    postgres_db: str = "task_assistant"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def sync_database_url(self) -> str:
        """Used by Alembic (sync driver)."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_url: str = "redis://localhost:6379/0"

    # ── Celery ─────────────────────────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── Telegram ───────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""  # empty = polling mode

    @computed_field
    @property
    def telegram_use_polling(self) -> bool:
        return not bool(self.telegram_webhook_url)

    # ── Discord ────────────────────────────────────────────────────────────────
    discord_bot_token: str = ""

    # ── LLM Provider ───────────────────────────────────────────────────────────
    llm_provider: Literal["openai", "gemini", "anthropic", "ollama", "huggingface"] = "openai"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 1024
    openai_temperature: float = 0.7

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # HuggingFace (local)
    hf_model_name: str = "mistralai/Mistral-7B-Instruct-v0.2"
    hf_device: Literal["cpu", "cuda", "mps"] = "cpu"

    # ── Scheduler ──────────────────────────────────────────────────────────────
    task_evaluation_interval_minutes: int = 5
    default_summary_time: str = "08:00"  # HH:MM 24-hour format

    # ── Business Rules ─────────────────────────────────────────────────────────
    max_extensions: int = Field(default=2, ge=1, le=5)
    max_ignored_prompts: int = Field(default=3, ge=1, le=10)
    nudge_cooldown_minutes: int = Field(default=30, ge=5)

    # AT_RISK thresholds (percentage of task time remaining)
    at_risk_threshold_1: int = Field(default=35, ge=1, le=99)  # first nudge
    at_risk_threshold_2: int = Field(default=10, ge=1, le=99)  # second nudge


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import this everywhere."""
    return Settings()


settings = get_settings()
