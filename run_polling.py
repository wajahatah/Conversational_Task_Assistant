"""
Telegram Long-Polling Runner — used for local development.

Run this instead of uvicorn when testing locally without a public URL.
It uses python-telegram-bot's polling mode and routes updates through
the same webhook handler logic.

Usage:
    python run_polling.py
"""

from __future__ import annotations

import asyncio
import sys

# asyncpg is incompatible with Windows ProactorEventLoop (default on Win 3.8+).
# Switch to SelectorEventLoop before any event loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import structlog
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters

from app.config import settings
from app.database.session import AsyncSessionLocal
from app.handlers.webhook_handler import _process_update

logger = structlog.get_logger(__name__)


async def handle_update(update, context):
    """Bridge between python-telegram-bot and our webhook handler."""
    print(f"DEBUG: Received update {update.update_id}")
    logger.info("Received update from Telegram", update_id=update.update_id)
    try:
        payload = update.to_dict()
        async with AsyncSessionLocal() as db:
            await _process_update(payload, db)
    except Exception as exc:
        print(f"DEBUG: Error in handle_update: {exc}")
        logger.error("Error in handle_update", error=str(exc), exc_info=True)


def main():
    logger.info("Starting Telegram polling mode...")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    async def error_handler(update, context):
        logger.error("Exception while handling an update:", exc_info=context.error)

    app.add_error_handler(error_handler)

    # Route all text messages and callback queries through our handler
    app.add_handler(MessageHandler(filters.ALL, handle_update))
    app.add_handler(CallbackQueryHandler(handle_update))

    from app.database.session import engine
    logger.info("Bot is running. Press Ctrl+C to stop.")
    try:
        app.run_polling(drop_pending_updates=False)
    finally:
        # Graceful shutdown: close DB engine
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
