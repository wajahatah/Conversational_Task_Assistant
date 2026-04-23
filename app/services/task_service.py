"""
Task Service — CRUD + Natural Language Parsing.

Handles all task lifecycle operations:
- Create task from natural language input (via LLM)
- Update task state
- Query tasks for a user
- Extend task deadline
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytz
import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Task, TaskState, User
from app.llm import get_llm_provider, ParsedTask

logger = structlog.get_logger(__name__)


class TaskValidationError(Exception):
    """Raised when a natural language input cannot form a valid task."""


async def create_task_from_nl(
    db: AsyncSession,
    user: User,
    user_message: str,
) -> Task:
    """
    Parse a natural language task message and persist it to the database.

    Args:
        db:           Async DB session.
        user:         The requesting User ORM object.
        user_message: Raw natural language from the user.

    Returns:
        The created Task object.

    Raises:
        TaskValidationError: if the LLM cannot parse a valid task.
    """
    llm = get_llm_provider()
    user_timezone = user.timezone or "UTC"

    # Use the user's local date, not UTC — matters near midnight for non-UTC users
    try:
        tz = pytz.timezone(user_timezone)
    except pytz.exceptions.UnknownTimeZoneError:
        tz = pytz.UTC
    today = datetime.now(tz=tz).strftime("%Y-%m-%d")

    try:
        parsed: ParsedTask = await llm.parse_task(
            user_message=user_message,
            user_timezone=user_timezone,
            current_date=today,
        )
    except ValueError as exc:
        # LLM returned a response but couldn't extract a valid task
        logger.warning("Task parse failed", error=str(exc), message=user_message)
        raise TaskValidationError(
            "I couldn't extract a task from that\\. "
            "Try: _\"Finish report from 2pm to 5pm\"_"
        ) from exc
    except Exception as exc:
        # API/network/auth error — not a task format issue
        logger.error("LLM service error", error=str(exc), message=user_message)
        raise TaskValidationError(
            "⚠️ AI service is unavailable right now\\. Please try again in a moment\\."
        ) from exc

    # Validate parsed times
    start_dt = _parse_iso(parsed.start_time)
    deadline_dt = _parse_iso(parsed.deadline)

    if start_dt is None or deadline_dt is None:
        raise TaskValidationError("Could not parse task times\\. Please include a clear start and end time\\.")

    if deadline_dt <= start_dt:
        raise TaskValidationError("The deadline must be *after* the start time\\.")

    if (deadline_dt - start_dt).total_seconds() < 300:  # minimum 5 minutes
        raise TaskValidationError("Tasks must be at least 5 minutes long\\.")

    task = Task(
        user_id=user.id,
        title=parsed.title,
        description=parsed.description,
        task_date=start_dt.date(),
        start_time=start_dt,
        deadline=deadline_dt,
        state=TaskState.NOT_STARTED,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    logger.info(
        "Task created",
        task_id=str(task.id),
        title=task.title,
        start=str(task.start_time),
        deadline=str(task.deadline),
    )
    return task


async def get_active_tasks(db: AsyncSession) -> list[Task]:
    """
    Fetch all tasks that are not in terminal state and not flagged no_more_action.
    Called by the scheduler's task evaluation job.
    """
    terminal_states = [TaskState.COMPLETED, TaskState.DROPPED]
    result = await db.execute(
        select(Task).where(
            and_(
                Task.state.notin_(terminal_states),
                Task.task_date == date.today(),
            )
        )
    )
    return list(result.scalars().all())


async def get_tasks_for_user_today(
    db: AsyncSession,
    user_id,
) -> list[Task]:
    """Get all of today's tasks for a specific user."""
    result = await db.execute(
        select(Task).where(
            and_(
                Task.user_id == user_id,
                Task.task_date == date.today(),
            )
        ).order_by(Task.start_time)
    )
    return list(result.scalars().all())


async def get_tasks_for_user_by_date(
    db: AsyncSession,
    user_id,
    task_date: date,
) -> list[Task]:
    """Get all tasks for a user on a specific date (used for daily summary)."""
    result = await db.execute(
        select(Task).where(
            and_(
                Task.user_id == user_id,
                Task.task_date == task_date,
            )
        ).order_by(Task.start_time)
    )
    return list(result.scalars().all())


async def update_task_state(
    db: AsyncSession,
    task: Task,
    new_state: TaskState,
) -> Task:
    """Update a task's state and persist."""
    task.state = new_state
    task.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(task)
    return task


async def extend_task(
    db: AsyncSession,
    task: Task,
    extra_minutes: int = 30,
) -> Task:
    """
    Extend a task's deadline by extra_minutes.
    Increments extensions_count — caller must check limit before calling.
    """
    task.deadline = task.deadline + timedelta(minutes=extra_minutes)
    task.extensions_count += 1
    task.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(task)
    logger.info("Task extended", task_id=str(task.id), new_deadline=str(task.deadline))
    return task


async def mark_no_more_action(db: AsyncSession, task: Task) -> Task:
    """Set the no_more_action flag after escalation has been sent."""
    task.no_more_action = True
    await db.commit()
    return task


async def increment_ignored_prompts(db: AsyncSession, task: Task) -> Task:
    """Called when a user ignores a nudge (no response within cooldown window)."""
    task.ignored_prompts += 1
    await db.commit()
    return task


def _parse_iso(dt_str: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string to a timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
