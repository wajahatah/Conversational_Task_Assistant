"""
Interaction Handler.

Processes poll responses and free-text messages from registered users.
Maps poll options (DONE, IN_PROGRESS, DROP, NEED_HELP) to system actions.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Interaction, ResponseType, Task, TaskState, User
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

    # Find the most recent unanswered interaction for this user
    result = await db.execute(
        select(Interaction)
        .join(Task, Interaction.task_id == Task.id)
        .where(
            Interaction.user_id == user.id,
            Interaction.response_type.is_(None),
            Task.state.notin_([TaskState.COMPLETED, TaskState.DROPPED]),
        )
        .order_by(Interaction.created_at.desc())
        .limit(1)
    )
    interaction = result.scalar_one_or_none()

    if not interaction:
        await send_plain_message(
            user.platform_id,
            "⚠️ No active task poll found\\. Send me a task to get started\\!",
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

    await _apply_response(db, user, task, response)


async def _apply_response(
    db: AsyncSession,
    user: User,
    task: Task,
    response: str,
) -> None:
    """Apply the business logic for each poll response."""
    user_settings = user.settings

    if response == "DONE":
        await update_task_state(db, task, TaskState.COMPLETED)
        await send_plain_message(
            user.platform_id,
            f"✅ Great work\\! *{_escape(task.title)}* marked as complete\\. 🎉",
        )

    elif response == "IN_PROGRESS":
        max_ext = user_settings.max_extensions if user_settings else 2
        if task.extensions_count >= max_ext:
            await send_plain_message(
                user.platform_id,
                f"⚠️ You've reached the maximum extensions for *{_escape(task.title)}*\\. "
                "The task will be reviewed at its original deadline\\.",
            )
        else:
            extended_task = await extend_task(db, task)
            await send_plain_message(
                user.platform_id,
                f"⏳ Got it\\! *{_escape(task.title)}* extended by 30 minutes\\. "
                f"New deadline: *{extended_task.deadline.strftime('%H:%M')}*\\. "
                "You've got this\\! 💪",
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
            Interaction.response_type == ResponseType.need_help,
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
            f"🕐 Start: *{task.start_time.strftime('%H:%M')}*\n"
            f"⏰ Deadline: *{task.deadline.strftime('%H:%M')}*\n\n"
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
            "/help \\- Show this help message\n\n"
            "Or just tell me your task:\n"
            "_\"Finish the report from 2pm to 5pm\"_",
        )

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
                    f"\\({t.start_time.strftime('%H:%M')} \\- {t.deadline.strftime('%H:%M')}\\)"
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
