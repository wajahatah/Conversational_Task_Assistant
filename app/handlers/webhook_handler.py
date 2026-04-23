"""
Webhook Handler — Main entry point for all incoming platform messages.

Routes messages based on user registration state:
- Unknown user → Registration Service
- Registering user → Registration Service (next step)
- Registered user → Interaction Handler
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.handlers.interaction_handler import (
    handle_command,
    handle_poll_response,
    handle_text_message,
)
from app.platform import get_platform_adapter
from app.platform.base import MessageType
from app.services.registration import (
    get_user_by_platform_id,
    process_registration_step,
    restart_registration,
    start_registration,
)

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive Telegram updates via webhook.
    Also used indirectly by the polling loop (processes same payload format).
    """
    payload = await request.json()
    return await _process_update(payload, db)


async def process_update_from_polling(payload: dict, db: AsyncSession) -> None:
    """Called by the polling loop for local development."""
    await _process_update(payload, db)


async def _process_update(payload: dict, db: AsyncSession) -> dict:
    adapter = get_platform_adapter()

    # ── Parse incoming message ─────────────────────────────────────────────
    try:
        msg = await adapter.parse_incoming(payload)
    except Exception as exc:
        logger.error("Failed to parse incoming update", error=str(exc), exc_info=True)
        return {"ok": True}

    if msg is None:
        logger.debug("Ignored update type or parsing failed", payload=payload)
        return {"ok": True}  # ignored update type

    platform_id = msg.platform_id
    logger.info("Processing message", platform_id=platform_id, message_type=msg.message_type)

    # ── Lookup user ────────────────────────────────────────────────────────
    try:
        user = await get_user_by_platform_id(db, platform_id)
    except Exception as exc:
        logger.error("DB error looking up user", platform_id=platform_id, error=str(exc))
        return {"ok": True}

    # ── Unknown user — start registration ─────────────────────────────────
    if user is None:
        logger.info("New user detected, starting registration", platform_id=platform_id)
        try:
            await start_registration(db, msg)
        except Exception as exc:
            logger.error("Registration start failed", platform_id=platform_id, error=str(exc))
        return {"ok": True}

    # ── Registering user — continue onboarding ────────────────────────────
    if user.registration_state is not None:
        # /start always restarts the registration flow from the beginning
        if msg.message_type == MessageType.COMMAND and msg.command == "start":
            try:
                await restart_registration(db, user, msg)
            except Exception as exc:
                logger.error("Registration restart failed", platform_id=platform_id, error=str(exc))
            return {"ok": True}

        try:
            await process_registration_step(db, user, msg)
        except Exception as exc:
            logger.error("Registration step failed", platform_id=platform_id, error=str(exc))
        return {"ok": True}

    # ── Fully registered user — route to appropriate handler ──────────────
    try:
        if msg.message_type == MessageType.POLL_RESPONSE:
            await handle_poll_response(db, user, msg)

        elif msg.message_type == MessageType.COMMAND:
            await handle_command(db, user, msg)

        elif msg.message_type == MessageType.TEXT:
            await handle_text_message(db, user, msg)

    except Exception as exc:
        logger.error(
            "Error handling message",
            platform_id=platform_id,
            error=str(exc),
            exc_info=True,
        )
        from app.services.notification_service import send_plain_message
        await send_plain_message(
            platform_id,
            "⚠️ Something went wrong\\. Please try again\\.",
        )

    return {"ok": True}
