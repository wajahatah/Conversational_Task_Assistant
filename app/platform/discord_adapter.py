"""
Discord Platform Adapter.

Handles all Discord-specific parsing and sending logic.
Uses discord.py v2 (async). Messages are sent as DMs to the user.

Formatting note: incoming message text from this codebase uses Telegram
MarkdownV2 escaping (e.g. \\! \\. \\( etc.).  _discord_format() strips
those escapes and converts *bold* → **bold** before sending.
"""

from __future__ import annotations

import re

import discord
import structlog

from app.platform.base import (
    BasePlatformAdapter,
    MessageType,
    NormalizedMessage,
    OutboundMessage,
    PollOption,
)

logger = structlog.get_logger(__name__)

POLL_BUTTON_LABELS: dict[str, str] = {
    "DONE": "✅ Done",
    "IN_PROGRESS": "🔄 In Progress",
    "DROP": "❌ Drop Task",
    "NEED_HELP": "🆘 Need Help",
}

POLL_BUTTON_STYLES: dict[str, discord.ButtonStyle] = {
    "DONE": discord.ButtonStyle.green,
    "IN_PROGRESS": discord.ButtonStyle.primary,
    "DROP": discord.ButtonStyle.danger,
    "NEED_HELP": discord.ButtonStyle.secondary,
}


class DiscordAdapter(BasePlatformAdapter):
    """
    Discord adapter — owns the discord.Client instance.

    run_discord.py retrieves the client via adapter.client, registers
    event handlers on it, then calls client.run(token).
    """

    def __init__(self) -> None:
        from app.config import settings as app_settings

        intents = discord.Intents.default()
        intents.message_content = True  # privileged intent — enable in Dev Portal
        self._client = discord.Client(intents=intents)
        self._token = app_settings.discord_bot_token

    @property
    def client(self) -> discord.Client:
        return self._client

    @property
    def token(self) -> str:
        return self._token

    @property
    def platform_name(self) -> str:
        return "discord"

    # ── Inbound ──────────────────────────────────────────────────────────────

    async def parse_incoming(self, payload: dict) -> NormalizedMessage | None:
        """
        Parse the synthetic payload dict created by run_discord.py.

        Two shapes:
          {"discord_type": "message",     "author_id": ..., "content": ...}
          {"discord_type": "interaction", "user_id": ...,   "component_id": ...}
        """
        discord_type = payload.get("discord_type")

        if discord_type == "interaction":
            user_id = payload.get("user_id", "")
            component_id = payload.get("component_id", "")
            if not user_id or not component_id:
                return None
            return NormalizedMessage(
                platform="discord",
                platform_id=user_id,
                message_type=MessageType.POLL_RESPONSE,
                poll_response=component_id,
                poll_message_id=payload.get("message_id"),
                raw=payload,
            )

        if discord_type == "message":
            author_id = payload.get("author_id", "")
            content = (payload.get("content") or "").strip()
            if not author_id or not content:
                return None

            # Slash-style commands: /start, /tasks, /help, /summary
            if content.startswith("/"):
                command = content.split()[0].lstrip("/").lower()
                return NormalizedMessage(
                    platform="discord",
                    platform_id=author_id,
                    message_type=MessageType.COMMAND,
                    text=content,
                    command=command,
                    raw=payload,
                )

            return NormalizedMessage(
                platform="discord",
                platform_id=author_id,
                message_type=MessageType.TEXT,
                text=content,
                raw=payload,
            )

        return None

    # ── Outbound ─────────────────────────────────────────────────────────────

    async def send_message(self, message: OutboundMessage) -> str | None:
        """Send a plain text message or a message with action buttons."""
        text = _discord_format(message.text)
        try:
            dm = await self._open_dm(message.platform_id)
            if message.poll_options:
                view = _build_view(message.poll_options)
                sent = await dm.send(text, view=view)
            else:
                sent = await dm.send(text)
            logger.info("Discord message sent", message_id=sent.id)
            return str(sent.id)
        except Exception as exc:
            logger.error(
                "Failed to send Discord message",
                platform_id=message.platform_id,
                error=str(exc),
                exc_info=True,
            )
            return None

    async def send_poll(
        self,
        platform_id: str,
        question: str,
        options: list[PollOption],
    ) -> str | None:
        """Send a question with inline action buttons (Discord has no native polls)."""
        try:
            dm = await self._open_dm(platform_id)
            view = _build_view(options)
            sent = await dm.send(_discord_format(question), view=view)
            logger.info("Discord poll sent", message_id=sent.id)
            return str(sent.id)
        except Exception as exc:
            logger.error(
                "Failed to send Discord poll",
                platform_id=platform_id,
                error=str(exc),
            )
            return None

    async def health_check(self) -> bool:
        return self._client.is_ready()

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _open_dm(self, platform_id: str) -> discord.DMChannel:
        user = await self._client.fetch_user(int(platform_id))
        return await user.create_dm()


# ── Formatting ────────────────────────────────────────────────────────────────

def _discord_format(text: str) -> str:
    """
    Convert Telegram MarkdownV2 text to Discord Markdown.

    Steps:
      1. Unescape MarkdownV2 backslash sequences (\\. \\! \\( etc.) → literal char
      2. Convert Telegram bold *text* → Discord bold **text**
         (single star not already doubled)
    """
    # Step 1 — remove Telegram escape backslashes
    text = re.sub(r'\\([_*\[\]()~`>#+\-=|{}.!\\])', r'\1', text)
    # Step 2 — promote single-star bold to Discord double-star bold
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'**\1**', text)
    return text


def _build_view(options: list[PollOption]) -> discord.ui.View:
    """Build a Discord View with one button per poll option."""
    view = discord.ui.View(timeout=600)  # 10-minute window to respond
    for opt in options:
        label = POLL_BUTTON_LABELS.get(opt.callback_data, opt.text)
        style = POLL_BUTTON_STYLES.get(opt.callback_data, discord.ButtonStyle.secondary)
        button = discord.ui.Button(
            label=label,
            custom_id=opt.callback_data,
            style=style,
        )
        view.add_item(button)
    return view
