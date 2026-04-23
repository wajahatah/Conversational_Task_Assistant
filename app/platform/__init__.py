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
    """
    Return the active platform adapter (cached singleton).

    Priority: Telegram → Discord.
    Set only ONE token in .env to control which platform is used.
    """
    from app.config import settings

    if settings.telegram_bot_token:
        from app.platform.telegram_adapter import TelegramAdapter
        return TelegramAdapter()

    if settings.discord_bot_token:
        from app.platform.discord_adapter import DiscordAdapter
        return DiscordAdapter()

    raise RuntimeError(
        "No platform configured. "
        "Set TELEGRAM_BOT_TOKEN or DISCORD_BOT_TOKEN in your .env file."
    )


__all__ = [
    "BasePlatformAdapter",
    "NormalizedMessage",
    "OutboundMessage",
    "PollOption",
    "MessageType",
    "get_platform_adapter",
]
