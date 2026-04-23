"""
Celery Application Setup.

Configures Celery with Redis as broker + result backend.
Celery Beat schedule is defined here for periodic jobs.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "task_assistant",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.scheduler.jobs"],
)

celery_app.conf.update(
    # ── Serialization ─────────────────────────────────────────────────────
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ── Timezone ──────────────────────────────────────────────────────────
    timezone="UTC",
    enable_utc=True,

    # ── Task behaviour ────────────────────────────────────────────────────
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    # ── Beat schedule ─────────────────────────────────────────────────────
    beat_schedule={
        # Task Evaluation Job — runs every N minutes
        "task-evaluation": {
            "task": "app.scheduler.jobs.evaluate_all_tasks",
            "schedule": settings.task_evaluation_interval_minutes * 60,  # seconds
        },

        # Daily Summary Dispatcher — runs every minute, checks per-user times
        # We run it frequently so each user's custom summary time is respected.
        "daily-summary-dispatcher": {
            "task": "app.scheduler.jobs.dispatch_daily_summaries",
            "schedule": crontab(minute="*"),  # every minute
        },
    },
)
