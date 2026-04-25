"""
Interaction Handler.

Processes poll responses and free-text messages from registered users.
Maps poll options (DONE, IN_PROGRESS, DROP, NEED_HELP) to system actions.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Interaction, InteractionType, ResponseType, Task, TaskState, User
from app.platform.base import NormalizedMessage
from app.services import orchestrator
from app.services.notification_service import send_plain_message
from app.services.task_service import (
    TaskValidationError,
    create_task_from_nl,
    extend_task,
    get_tasks_for_user_today,
    update_task_state,
)

logger = structlog.get_logger(__name__)


def _fmt_time(dt: datetime, user: User) -> str:
    """Format a UTC-aware datetime as HH:MM in the user's local timezone."""
    try:
        tz = zoneinfo.ZoneInfo(user.timezone or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        tz = zoneinfo.ZoneInfo("UTC")
    return dt.astimezone(tz).strftime("%H:%M")


async def handle_poll_response(
    db: AsyncSession,
    user: User,
    msg: NormalizedMessage,
) -> None:
    """
    Handle a user's poll button tap (DONE, IN_PROGRESS, DROP, NEED_HELP).

    Looks up the most recent unanswered poll for this user, applies the response.
    """
    response = msg.poll_response
    if not response:
        return

    # Look up the SPECIFIC interaction the user clicked on, by message_id.
    # Without this, every click would resolve to "the most recent unanswered
    # interaction" and a single button tap could complete an unrelated task.
    interaction: Interaction | None = None
    if msg.poll_message_id:
        result = await db.execute(
            select(Interaction).where(
                Interaction.user_id == user.id,
                Interaction.message_id == msg.poll_message_id,
            )
        )
        interaction = result.scalar_one_or_none()

    if not interaction:
        await send_plain_message(
            user.platform_id,
            "⚠️ This poll is no longer active\\. Send me a task to get started\\!",
        )
        return

    if interaction.response_type is not None:
        await send_plain_message(
            user.platform_id,
            "⚠️ You've already responded to this prompt\\.",
        )
        return

    # Load the associated task
    task_result = await db.execute(
        select(Task).where(Task.id == interaction.task_id)
    )
    task = task_result.scalar_one_or_none()
    if not task:
        return

    # Record the response on the interaction
    interaction.response_type = ResponseType(response.lower())
    await db.commit()

    await _apply_response(db, user, task, interaction, response)


async def _apply_response(
    db: AsyncSession,
    user: User,
    task: Task,
    interaction: Interaction,
    response: str,
) -> None:
    """Apply the business logic for each poll response."""
    if response == "DONE":
        await update_task_state(db, task, TaskState.COMPLETED)
        await send_plain_message(
            user.platform_id,
            f"✅ Great work\\! *{_escape(task.title)}* marked as complete\\. 🎉",
        )

    elif response == "STALL":
        await update_task_state(db, task, TaskState.STALLED)
        await send_plain_message(
            user.platform_id,
            f"🔴 *{_escape(task.title)}* marked as stalled\\. "
            "I'll include it in tomorrow's summary\\.",
        )

    elif response == "IN_PROGRESS":
        if interaction.type == InteractionType.URGENT_POLL:
            # Second nudge (10% remaining) — extend by 50% of original duration
            original_seconds = (task.deadline - task.start_time).total_seconds()
            extra_minutes = max(5, int(original_seconds * 0.5 / 60))
            extended_task = await extend_task(db, task, extra_minutes=extra_minutes)
            await send_plain_message(
                user.platform_id,
                f"⏳ Got it\\! *{_escape(task.title)}* extended by {extra_minutes} minutes\\. "
                f"New deadline: *{_fmt_time(extended_task.deadline, user)}*\\. "
                "You've got this\\! 💪",
            )
        else:
            # First nudge or start reminder — acknowledge only, no extension
            await send_plain_message(
                user.platform_id,
                f"👍 Keep it up\\! *{_escape(task.title)}* \\— "
                f"deadline: *{_fmt_time(task.deadline, user)}*\\.",
            )

    elif response == "DROP":
        await update_task_state(db, task, TaskState.DROPPED)
        await send_plain_message(
            user.platform_id,
            f"❌ *{_escape(task.title)}* has been dropped\\. "
            "It'll appear in your daily summary\\.",
        )

    elif response == "NEED_HELP":
        await send_plain_message(
            user.platform_id,
            f"🆘 Let's work through *{_escape(task.title)}* together\\. What's blocking you?",
        )
        await orchestrator.handle_help_request(
            db=db,
            task=task,
            platform_id=user.platform_id,
        )


async def handle_text_message(
    db: AsyncSession,
    user: User,
    msg: NormalizedMessage,
) -> None:
    """
    Handle a free-text message from a registered user.

    Two cases:
    1. User is in an active HELP session → route to Orchestrator
    2. User is adding a new task → parse and create
    """
    text = msg.text or ""

    # Check if user has an active HELP session (most recent interaction = NEED_HELP)
    result = await db.execute(
        select(Interaction)
        .join(Task, Interaction.task_id == Task.id)
        .where(
            Interaction.user_id == user.id,
            Interaction.response_type == ResponseType.NEED_HELP,
            Task.state == TaskState.ACTIVE,
        )
        .order_by(Interaction.created_at.desc())
        .limit(1)
    )
    active_help = result.scalar_one_or_none()

    if active_help:
        # Route to orchestrator for continued HELP conversation
        task_result = await db.execute(
            select(Task).where(Task.id == active_help.task_id)
        )
        task = task_result.scalar_one_or_none()
        if task:
            await orchestrator.handle_help_request(
                db=db,
                task=task,
                platform_id=user.platform_id,
                user_message=text,
            )
            return

    # Otherwise treat as a new task
    try:
        task = await create_task_from_nl(db, user, text)
        await send_plain_message(
            user.platform_id,
            f"📋 Task added\\!\n\n"
            f"*{_escape(task.title)}*\n"
            f"🕐 Start: *{_fmt_time(task.start_time, user)}*\n"
            f"⏰ Deadline: *{_fmt_time(task.deadline, user)}*\n\n"
            "I'll remind you when it's time\\. Good luck\\! 💪",
        )
    except TaskValidationError as exc:
        await send_plain_message(user.platform_id, f"⚠️ {exc}")
    except Exception as exc:
        logger.error("Unexpected error creating task", error=str(exc))
        await send_plain_message(
            user.platform_id,
            "⚠️ Something went wrong\\. Please try again\\.",
        )


async def handle_command(
    db: AsyncSession,
    user: User,
    msg: NormalizedMessage,
) -> None:
    """Handle bot commands like /tasks, /help, /summary."""
    command = msg.command or ""

    if command in ("start", "help"):
        await send_plain_message(
            user.platform_id,
            "👋 *Task Assistant Commands:*\n\n"
            "/tasks \\- View today's tasks\n"
            "/summary \\- Get your daily summary now\n"
            "/register \\- Update your settings (Name, Timezone, etc.)\n"
            "/help \\- Show this help message\n\n"
            "Or just tell me your task:\n"
            "_\"Finish the report from 2pm to 5pm\"_",
        )

    elif command == "register":
        from app.services.registration import restart_registration
        await restart_registration(db, user, msg)

    elif command == "tasks":
        tasks = await get_tasks_for_user_today(db, user.id)
        if not tasks:
            await send_plain_message(
                user.platform_id,
                "📋 No tasks added for today\\. Send me a task to get started\\!",
            )
        else:
            lines = ["📋 *Your tasks for today:*\n"]
            state_emoji = {
                TaskState.NOT_STARTED: "⏳",
                TaskState.ACTIVE: "🔄",
                TaskState.AT_RISK: "⚠️",
                TaskState.STALLED: "🔴",
                TaskState.COMPLETED: "✅",
                TaskState.DROPPED: "❌",
            }
            for t in tasks:
                emoji = state_emoji.get(t.state, "•")
                lines.append(
                    f"{emoji} *{_escape(t.title)}* "
                    f"\\({_fmt_time(t.start_time, user)} \\- {_fmt_time(t.deadline, user)}\\)"
                )
            await send_plain_message(user.platform_id, "\n".join(lines))

    elif command == "summary":
        from app.services.summary_service import send_daily_summary
        await send_daily_summary(db, user)

    else:
        await send_plain_message(
            user.platform_id,
            f"Unknown command: `/{_escape(command)}`\\. Try /help",
        )


def _escape(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
