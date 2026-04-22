"""
Telegram Platform Adapter.

Handles all Telegram-specific parsing and sending logic.
Uses python-telegram-bot v21 (async).
"""

from __future__ import annotations

import structlog
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode

from app.config import settings
from app.platform.base import (
    BasePlatformAdapter,
    MessageType,
    NormalizedMessage,
    OutboundMessage,
    PollOption,
)

logger = structlog.get_logger(__name__)

# Map callback data values to human-friendly button labels
POLL_OPTION_LABELS: dict[str, str] = {
    "DONE": "✅ Done",
    "IN_PROGRESS": "🔄 In Progress",
    "DROP": "❌ Drop Task",
    "NEED_HELP": "🆘 Need Help",
}


class TelegramAdapter(BasePlatformAdapter):
    def __init__(self) -> None:
        self._bot = Bot(token=settings.telegram_bot_token)

    @property
    def platform_name(self) -> str:
        return "telegram"

    async def parse_incoming(self, payload: dict) -> NormalizedMessage | None:
        """
        Parse a Telegram Update dict into a NormalizedMessage.

        Handles:
        - Regular text messages
        - Bot commands (/start, /tasks, etc.)
        - Inline keyboard callback queries (poll responses)
        """
        try:
            update = Update.de_json(payload, self._bot)
        except Exception as exc:
            logger.warning("Failed to parse Telegram update", error=str(exc))
            return None

        # ── Callback query (poll/button response) ──────────────────────────
        if update.callback_query:
            query = update.callback_query
            await query.answer()  # Dismiss the loading spinner on Telegram side
            platform_id = str(query.from_user.id)
            return NormalizedMessage(
                platform="telegram",
                platform_id=platform_id,
                message_type=MessageType.POLL_RESPONSE,
                poll_response=query.data,
                poll_message_id=str(query.message.message_id) if query.message else None,
                raw=payload,
            )

        # ── Text message ───────────────────────────────────────────────────
        if update.message and update.message.text:
            msg = update.message
            platform_id = str(msg.from_user.id)
            text = msg.text.strip()

            # Bot command (starts with /)
            if text.startswith("/"):
                command = text.split()[0].lstrip("/").split("@")[0]  # strip @botname
                return NormalizedMessage(
                    platform="telegram",
                    platform_id=platform_id,
                    message_type=MessageType.COMMAND,
                    text=text,
                    command=command,
                    raw=payload,
                )

            return NormalizedMessage(
                platform="telegram",
                platform_id=platform_id,
                message_type=MessageType.TEXT,
                text=text,
                raw=payload,
            )

        # Ignore other update types (stickers, photos, etc.)
        return None

    async def send_message(self, message: OutboundMessage) -> str | None:
        """Send a plain text message or a message with inline keyboard buttons."""
        print(f"DEBUG: Attempting to send message to {message.platform_id}")
        logger.debug("Sending message", platform_id=message.platform_id, text=message.text)
        try:
            if message.poll_options:
                # Render options as inline keyboard buttons (2 per row)
                keyboard = self._build_keyboard(message.poll_options)
                sent = await self._bot.send_message(
                    chat_id=message.platform_id,
                    text=message.text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                sent = await self._bot.send_message(
                    chat_id=message.platform_id,
                    text=message.text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            logger.info("Message sent successfully", message_id=sent.message_id)
            return str(sent.message_id)
        except Exception as exc:
            print(f"DEBUG: Failed to send Telegram message: {exc}")
            logger.error(
                "Failed to send Telegram message",
                platform_id=message.platform_id,
                text=message.text,
                error=str(exc),
                exc_info=True
            )
            return None

    async def send_poll(
        self,
        platform_id: str,
        question: str,
        options: list[PollOption],
    ) -> str | None:
        """
        Send a task status poll as an inline keyboard.
        Telegram native polls don't support callbacks, so we use inline buttons.
        """
        keyboard = self._build_keyboard(options)
        try:
            sent = await self._bot.send_message(
                chat_id=platform_id,
                text=question,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return str(sent.message_id)
        except Exception as exc:
            logger.error(
                "Failed to send Telegram poll",
                platform_id=platform_id,
                error=str(exc),
            )
            return None

    def _build_keyboard(self, options: list[PollOption]) -> InlineKeyboardMarkup:
        """Build an InlineKeyboardMarkup with 2 buttons per row."""
        buttons = [
            InlineKeyboardButton(
                text=POLL_OPTION_LABELS.get(opt.callback_data, opt.text),
                callback_data=opt.callback_data,
            )
            for opt in options
        ]
        # Group into rows of 2
        rows = [buttons[i: i + 2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(rows)

    async def health_check(self) -> bool:
        try:
            me = await self._bot.get_me()
            return bool(me.id)
        except Exception:
            return False
