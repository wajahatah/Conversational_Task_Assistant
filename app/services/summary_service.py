"""
Daily Summary Service.

Generates and sends the per-user morning briefing.
Called by the Daily Summary Celery job.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TaskState, User
from app.llm import get_llm_provider
from app.llm.prompts import DAILY_SUMMARY_SYSTEM, DAILY_SUMMARY_USER
from app.platform import get_platform_adapter, OutboundMessage
from app.services.task_service import get_tasks_for_user_by_date

logger = structlog.get_logger(__name__)


async def send_daily_summary(db: AsyncSession, user: User) -> None:
    """
    Build and send a daily summary to the user.

    Covers:
    - Yesterday's completed, stalled, and dropped tasks
    - Today's upcoming tasks
    - Yesterday's completion rate

    Args:
        db:   Async DB session.
        user: The User to send the summary to.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)

    yesterday_tasks = await get_tasks_for_user_by_date(db, user.id, yesterday)
    todays_tasks = await get_tasks_for_user_by_date(db, user.id, today)

    # ── Categorise yesterday's tasks ──────────────────────────────────────
    completed = [t for t in yesterday_tasks if t.state == TaskState.COMPLETED]
    stalled = [t for t in yesterday_tasks if t.state == TaskState.STALLED]
    dropped = [t for t in yesterday_tasks if t.state == TaskState.DROPPED]

    total = len(yesterday_tasks)
    completion_rate = round((len(completed) / total * 100) if total > 0 else 0)

    # ── Format task lists ──────────────────────────────────────────────────
    def fmt(tasks) -> str:
        if not tasks:
            return "None"
        return ", ".join(t.title for t in tasks)

    completed_str = fmt(completed)
    stalled_str = fmt(stalled)
    dropped_str = fmt(dropped)
    todays_str = fmt(todays_tasks) if todays_tasks else "No tasks added yet"

    # ── Use LLM to generate a friendly summary message ────────────────────
    llm = get_llm_provider()
    user_prompt = DAILY_SUMMARY_USER.format(
        completed_tasks=completed_str,
        stalled_tasks=stalled_str,
        dropped_tasks=dropped_str,
        todays_tasks=todays_str,
        completion_rate=completion_rate,
    )

    try:
        response = await llm.generate(
            prompt=user_prompt,
            system_prompt=DAILY_SUMMARY_SYSTEM,
        )
        summary_text = response.text
    except Exception as exc:
        logger.error("LLM summary generation failed", error=str(exc))
        # Fallback to plain text summary
        summary_text = _plain_summary(
            completed_str, stalled_str, dropped_str, todays_str, completion_rate
        )

    adapter = get_platform_adapter()
    await adapter.send_message(OutboundMessage(
        platform_id=user.platform_id,
        text=_escape(summary_text),
    ))

    logger.info(
        "Daily summary sent",
        user_id=str(user.id),
        completion_rate=completion_rate,
        tasks_today=len(todays_tasks),
    )


def _plain_summary(
    completed: str,
    stalled: str,
    dropped: str,
    today: str,
    rate: int,
) -> str:
    return (
        f"Good morning! Here's your daily briefing:\n\n"
        f"Yesterday:\n"
        f"✅ Completed: {completed}\n"
        f"⚠️ Stalled: {stalled}\n"
        f"❌ Dropped: {dropped}\n"
        f"📊 Completion rate: {rate}%\n\n"
        f"Today's tasks:\n📋 {today}\n\n"
        f"Let's make today count!"
    )


def _escape(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
