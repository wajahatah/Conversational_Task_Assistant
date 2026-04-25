"""
Discord Bot Runner — for local development and testing.

Discord bots use a persistent WebSocket connection (not webhooks).
This runner owns the event loop and routes every Discord event
through the same _process_update pipeline used by the Telegram bot.

Usage:
    python run_discord.py

Prerequisites in Discord Developer Portal (discord.com/developers):
  1. Create an application → add a Bot.
  2. Under Bot → Privileged Gateway Intents, enable:
       • MESSAGE CONTENT INTENT
  3. Copy the bot token → DISCORD_BOT_TOKEN in .env
  4. Invite the bot to your server:
       OAuth2 → URL Generator → scope: bot
       Permissions: Send Messages, Read Message History
  5. DM the bot directly to test (or message it in a channel).
"""

from __future__ import annotations

import asyncio
import sys

# asyncpg is incompatible with Windows ProactorEventLoop (default on Win 3.8+).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import structlog
import discord

from app.config import settings
from app.database.session import AsyncSessionLocal, engine
from app.handlers.webhook_handler import _process_update
from app.platform import get_platform_adapter
from app.platform.discord_adapter import DiscordAdapter
from app.scheduler.jobs import _evaluate_all_tasks_async, _dispatch_daily_summaries_async

logger = structlog.get_logger(__name__)

_background_loops_started = False


async def _evaluation_loop() -> None:
    """Evaluate task states and fire reminders every 60 seconds."""
    logger.info("Task evaluation loop started")
    while True:
        try:
            await _evaluate_all_tasks_async()
        except Exception as exc:
            logger.error("Evaluation loop error", error=str(exc))
        await asyncio.sleep(60)


async def _summary_loop() -> None:
    """Check and dispatch daily summaries every 60 seconds."""
    while True:
        try:
            await _dispatch_daily_summaries_async()
        except Exception as exc:
            logger.error("Summary loop error", error=str(exc))
        await asyncio.sleep(60)


def main() -> None:
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in .env")

    # get_platform_adapter() returns a DiscordAdapter when only DISCORD_BOT_TOKEN is set.
    adapter = get_platform_adapter()
    if not isinstance(adapter, DiscordAdapter):
        raise RuntimeError(
            "Active adapter is not DiscordAdapter. "
            "Unset TELEGRAM_BOT_TOKEN in .env so Discord takes priority."
        )

    client = adapter.client

    # ── Event handlers ───────────────────────────────────────────────────────

    @client.event
    async def on_ready() -> None:
        global _background_loops_started
        logger.info("Discord bot ready", username=str(client.user))
        print(f"Discord bot logged in as {client.user}. Send it a DM to start!")
        if not _background_loops_started:
            _background_loops_started = True
            asyncio.create_task(_evaluation_loop())
            asyncio.create_task(_summary_loop())
            logger.info("Background evaluation and summary loops started")

    @client.event
    async def on_message(message: discord.Message) -> None:
        # Ignore the bot's own messages
        if message.author == client.user:
            return

        # Accept DMs and any server channel the bot can read
        print(f"DEBUG: Discord message from {message.author.id}: {message.content!r}")
        logger.info("Discord message received", user_id=str(message.author.id))

        payload = {
            "discord_type": "message",
            "author_id": str(message.author.id),
            "content": message.content,
            "message_id": str(message.id),
            "channel_id": str(message.channel.id),
        }

        try:
            async with AsyncSessionLocal() as db:
                await _process_update(payload, db)
        except Exception as exc:
            print(f"DEBUG: Error handling Discord message: {exc}")
            logger.error("Error handling Discord message", error=str(exc), exc_info=True)

    @client.event
    async def on_interaction(interaction: discord.Interaction) -> None:
        # Only handle button / select-menu component interactions
        if interaction.type != discord.InteractionType.component:
            return

        component_id = (interaction.data or {}).get("custom_id", "")
        print(f"DEBUG: Discord interaction from {interaction.user.id}: {component_id}")
        logger.info(
            "Discord interaction received",
            user_id=str(interaction.user.id),
            component=component_id,
        )

        # Acknowledge immediately — Discord requires a response within 3 seconds
        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass  # already acknowledged

        payload = {
            "discord_type": "interaction",
            "user_id": str(interaction.user.id),
            "component_id": component_id,
            "message_id": str(interaction.message.id) if interaction.message else None,
        }

        try:
            async with AsyncSessionLocal() as db:
                await _process_update(payload, db)
        except Exception as exc:
            print(f"DEBUG: Error handling Discord interaction: {exc}")
            logger.error("Error handling Discord interaction", error=str(exc), exc_info=True)

    # ── Start ────────────────────────────────────────────────────────────────

    logger.info("Starting Discord bot...")
    try:
        client.run(adapter.token)
    finally:
        # Dispose DB engine on shutdown
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
