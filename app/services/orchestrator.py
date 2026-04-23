"""
Conversation Orchestrator.

Bridge between the rule engine and the LLM.
Manages the HELP flow when a user selects NEED_HELP on a task poll.
Stores and retrieves conversation history for multi-turn interactions.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ConversationHistory, ConversationRole, Task
from app.llm import get_llm_provider, Message
from app.llm.prompts import HELP_SYSTEM
from app.platform import get_platform_adapter, OutboundMessage

logger = structlog.get_logger(__name__)

# Max history turns to include in LLM context (keep costs reasonable)
MAX_HISTORY_TURNS = 10


async def handle_help_request(
    db: AsyncSession,
    task: Task,
    platform_id: str,
    user_message: str | None = None,
) -> None:
    """
    Enter or continue a HELP flow for the given task.

    If user_message is None, this is the initial help trigger (user selected NEED_HELP).
    If user_message is provided, it's a follow-up message in the ongoing help session.

    Args:
        db:           Async DB session.
        task:         The task the user needs help with.
        platform_id:  Platform user ID for sending response.
        user_message: User's follow-up message, or None for initial trigger.
    """
    llm = get_llm_provider()
    adapter = get_platform_adapter()

    # ── Fetch conversation history for this task ───────────────────────────
    history = await _get_history(db, task.user_id, task.id)

    # ── If initial trigger, generate opening message ───────────────────────
    if user_message is None:
        user_message = f"I need help with my task: {task.title}"

    # ── Build system prompt with task context ──────────────────────────────
    time_remaining = _format_remaining(task)
    system = HELP_SYSTEM.format(
        task_title=task.title,
        deadline=task.deadline.strftime("%H:%M"),
        time_remaining=time_remaining,
    )

    # ── Generate LLM response ──────────────────────────────────────────────
    response = await llm.generate(
        prompt=user_message,
        history=history,
        system_prompt=system,
    )

    # ── Persist both turns to ConversationHistory ─────────────────────────
    await _save_message(db, task.user_id, task.id, ConversationRole.USER, user_message)
    await _save_message(db, task.user_id, task.id, ConversationRole.ASSISTANT, response.text)

    # ── Send response to user ──────────────────────────────────────────────
    await adapter.send_message(OutboundMessage(
        platform_id=platform_id,
        text=_escape(response.text),
    ))

    logger.info(
        "Help response sent",
        task_id=str(task.id),
        provider=response.provider,
        tokens_out=response.output_tokens,
    )


async def _get_history(
    db: AsyncSession,
    user_id,
    task_id,
) -> list[Message]:
    """Fetch the last N turns of conversation history for a task."""
    result = await db.execute(
        select(ConversationHistory)
        .where(
            ConversationHistory.user_id == user_id,
            ConversationHistory.task_id == task_id,
        )
        .order_by(ConversationHistory.created_at.desc())
        .limit(MAX_HISTORY_TURNS)
    )
    rows = list(reversed(result.scalars().all()))
    return [Message(role=row.role.value, content=row.message) for row in rows]


async def _save_message(
    db: AsyncSession,
    user_id,
    task_id,
    role: ConversationRole,
    message: str,
) -> None:
    record = ConversationHistory(
        user_id=user_id,
        task_id=task_id,
        role=role,
        message=message,
    )
    db.add(record)
    await db.commit()


def _format_remaining(task: Task) -> str:
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc)
    deadline = task.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    remaining = deadline - now
    minutes = int(remaining.total_seconds() / 60)
    if minutes < 0:
        return "deadline passed"
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _escape(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
