"""
Celery Jobs.

Two jobs:
1. evaluate_all_tasks  — State inference + intervention for all active tasks
2. dispatch_daily_summaries — Send daily summary to users whose time has come
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time, timezone

import pytz
import structlog
from celery.signals import worker_process_shutdown
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings as app_settings
from app.database.models import User
from app.database.session import engine
from app.scheduler.celery_app import celery_app
from app.services.intervention_engine import ActionType, decide_intervention
from app.services.notification_service import dispatch_intervention
from app.services.state_engine import infer_state, should_transition
from app.services.summary_service import send_daily_summary
from app.services.task_service import (
    get_active_tasks,
    mark_no_more_action,
    update_task_state,
)

# asyncpg is incompatible with Windows ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = structlog.get_logger(__name__)

# NullPool: no connection caching across event loops — each Celery task gets
# fresh DB connections in its own dedicated event loop, preventing the
# "Future attached to a different loop" / ProactorEventLoop errors on Windows.
_jobs_engine = create_async_engine(app_settings.database_url, poolclass=NullPool, echo=False)
_JobsSession = async_sessionmaker(
    bind=_jobs_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


def run_async(coro):
    """Run an async coroutine from a synchronous Celery task.

    Creates a dedicated event loop per invocation so that asyncpg connections
    are always bound to the current loop (no cross-loop Future errors).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.scheduler.jobs.evaluate_all_tasks", bind=True, max_retries=3)
def evaluate_all_tasks(self):
    """
    Task Evaluation Job.

    For each active task:
    1. Infer current state
    2. Decide intervention
    3. Execute intervention (send notification)
    4. Update task state in DB
    """
    run_async(_evaluate_all_tasks_async())


async def _evaluate_all_tasks_async():
    async with _JobsSession() as db:
        tasks = await get_active_tasks(db)
        logger.info("Evaluating tasks", count=len(tasks))

        for task in tasks:
            try:
                user_settings = task.user.settings if task.user else None
                state_result = infer_state(task, user_settings)
                decision = decide_intervention(task, state_result, user_settings)

                # Apply state transition if changed
                if should_transition(task.state, state_result.state):
                    await update_task_state(db, task, state_result.state)

                # Dispatch intervention (sends message + logs interaction)
                platform_id = task.user.platform_id if task.user else None
                if platform_id and decision.action.value != "no_action":
                    await dispatch_intervention(db, task, decision, platform_id)

                    # If STALLED escalation was sent, flag no_more_action
                    if decision.action == ActionType.SEND_ESCALATION:
                        await mark_no_more_action(db, task)

            except Exception as exc:
                logger.error(
                    "Error evaluating task",
                    task_id=str(task.id),
                    error=str(exc),
                )


@celery_app.task(name="app.scheduler.jobs.dispatch_daily_summaries", bind=True, max_retries=3)
def dispatch_daily_summaries(self):
    """
    Daily Summary Dispatcher.

    Runs every minute. Checks each registered user's summary_trigger_time
    against their local time. If it matches the current minute, send summary.
    """
    run_async(_dispatch_daily_summaries_async())


async def _dispatch_daily_summaries_async():
    now_utc = datetime.now(tz=timezone.utc)

    async with _JobsSession() as db:
        # Fetch all fully registered users
        result = await db.execute(
            select(User).where(User.registration_state.is_(None))
        )
        users = list(result.scalars().all())

        for user in users:
            try:
                # Convert current UTC time to user's local time
                tz = pytz.timezone(user.timezone or "UTC")
                local_now = now_utc.astimezone(tz)
                local_time = local_now.time()

                # Get user's configured summary time (from settings or user default)
                trigger_time: time = (
                    user.settings.summary_trigger_time
                    if user.settings
                    else user.summary_trigger_time
                )

                # Check if current minute matches the trigger (ignore seconds)
                if (
                    local_time.hour == trigger_time.hour
                    and local_time.minute == trigger_time.minute
                ):
                    await send_daily_summary(db, user)

            except Exception as exc:
                logger.error(
                    "Error in daily summary for user",
                    user_id=str(user.id),
                    error=str(exc),
                )


@worker_process_shutdown.connect
def on_worker_process_shutdown(**kwargs):
    """
    Ensure the DB engine is disposed when the Celery worker process exits.
    This prevents connection leaks and Proactor loop errors on Windows.
    """
    logger.info("Celery worker process shutting down, disposing DB engine")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(engine.dispose())
    finally:
        loop.close()
