"""
Platform package.

Active adapter is determined by which bot is configured.
For now, only Telegram is implemented.
"""

from __future__ import annotations

from functools import lru_cache

from app.platform.base import (
    BasePlatformAdapter,
    NormalizedMessage,
    OutboundMessage,
    PollOption,
    MessageType,
)


@lru_cache(maxsize=1)
def get_platform_adapter() -> BasePlatformAdapter:
    """Return the active platform adapter (cached singleton)."""
    from app.config import settings
    from app.platform.telegram_adapter import TelegramAdapter

    if settings.telegram_bot_token:
        return TelegramAdapter()

    raise RuntimeError(
        "No platform configured. Set TELEGRAM_BOT_TOKEN in your .env file."
    )


__all__ = [
    "BasePlatformAdapter",
    "NormalizedMessage",
    "OutboundMessage",
    "PollOption",
    "MessageType",
    "get_platform_adapter",
]
