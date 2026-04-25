"""
Notification Service.

Translates intervention decisions into messages and sends them
via the Platform Adapter. Also logs each interaction to the database.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Interaction, InteractionType, Task
from app.platform import get_platform_adapter, OutboundMessage, PollOption
from app.services.intervention_engine import ActionType, InterventionDecision

logger = structlog.get_logger(__name__)

# ── Standard poll options sent with AT_RISK polls ─────────────────────────
STANDARD_POLL_OPTIONS = [
    PollOption(text="Done", callback_data="DONE"),
    PollOption(text="In Progress", callback_data="IN_PROGRESS"),
    PollOption(text="Drop Task", callback_data="DROP"),
    PollOption(text="Need Help", callback_data="NEED_HELP"),
]


async def dispatch_intervention(
    db: AsyncSession,
    task: Task,
    decision: InterventionDecision,
    platform_id: str,
) -> None:
    """
    Send the appropriate message/poll based on the intervention decision
    and log the interaction to the database.

    Args:
        db:          Async DB session.
        task:        The Task being acted on.
        decision:    Output from the Intervention Engine.
        platform_id: User's platform identifier (e.g. Telegram chat ID).
    """
    if decision.action == ActionType.NO_ACTION:
        return

    adapter = get_platform_adapter()
    message_id: str | None = None
    content: str = ""
    interaction_type = InteractionType.SYSTEM

    if decision.action == ActionType.SEND_START_REMINDER:
        content = (
            f"⏰ *Time to start:* {_escape(task.title)}\n\n"
            f"Your task begins now\\. Deadline: {_format_time(task.deadline)}\n\n"
            "How's it looking?"
        )
        message_id = await adapter.send_poll(
            platform_id=platform_id,
            question=content,
            options=STANDARD_POLL_OPTIONS,
        )
        interaction_type = InteractionType.REMINDER

    elif decision.action == ActionType.SEND_STATUS_POLL:
        pct = decision.reason.split("%")[0].split()[-1]
        content = (
            f"📊 *Quick check\\-in:* {_escape(task.title)}\n\n"
            f"You have about *{pct}%* of your time remaining\\. "
            "How's it going?"
        )
        message_id = await adapter.send_poll(
            platform_id=platform_id,
            question=content,
            options=STANDARD_POLL_OPTIONS,
        )
        interaction_type = InteractionType.POLL

    elif decision.action == ActionType.SEND_URGENT_POLL:
        content = (
            f"🚨 *Urgent check\\-in:* {_escape(task.title)}\n\n"
            "You're almost out of time\\! What's the status?"
        )
        message_id = await adapter.send_poll(
            platform_id=platform_id,
            question=content,
            options=STANDARD_POLL_OPTIONS,
        )
        interaction_type = InteractionType.POLL

    elif decision.action == ActionType.SEND_ESCALATION:
        content = (
            f"🔴 *Task stalled:* {_escape(task.title)}\n\n"
            "This task has been stalled\\. No further reminders will be sent\\.\n"
            "I'll include it in tomorrow's summary\\."
        )
        message_id = await adapter.send_message(OutboundMessage(
            platform_id=platform_id,
            text=content,
        ))
        interaction_type = InteractionType.SYSTEM

    # ── Log interaction ────────────────────────────────────────────────────
    if content:
        interaction = Interaction(
            task_id=task.id,
            user_id=task.user_id,
            type=interaction_type,
            content=content,
            message_id=message_id,
        )
        db.add(interaction)

        # Update task's last_response_time to track cooldown
        task.last_response_time = datetime.now(tz=timezone.utc)
        await db.commit()

        logger.info(
            "Intervention dispatched",
            action=decision.action,
            task_id=str(task.id),
            message_id=message_id,
        )


async def send_plain_message(platform_id: str, text: str) -> None:
    """Utility to send a plain text message without DB logging."""
    adapter = get_platform_adapter()
    await adapter.send_message(OutboundMessage(platform_id=platform_id, text=text))


def _format_time(dt: datetime, timezone_str: str = "UTC") -> str:
    """Format a UTC-aware datetime as HH:MM in the given timezone."""
    try:
        tz = zoneinfo.ZoneInfo(timezone_str)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        tz = zoneinfo.ZoneInfo("UTC")
    return _escape(dt.astimezone(tz).strftime("%H:%M"))


def _escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
