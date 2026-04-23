"""
Intervention Engine.

Decides WHAT action to take (if any) given the current task state.
Enforces cooldowns, action limits, and the business rule constraints.
Does NOT send messages — delegates to NotificationService.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

import structlog

from app.config import settings
from app.database.models import Task, TaskState
from app.services.state_engine import StateResult

logger = structlog.get_logger(__name__)


class ActionType(str, Enum):
    SEND_START_REMINDER = "send_start_reminder"
    SEND_STATUS_POLL = "send_status_poll"       # AT_RISK_1
    SEND_URGENT_POLL = "send_urgent_poll"        # AT_RISK_2
    SEND_ESCALATION = "send_escalation"          # STALLED
    NO_ACTION = "no_action"


@dataclass
class InterventionDecision:
    """Output of the Intervention Engine."""
    action: ActionType
    reason: str
    task_id: str | None = None


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def decide_intervention(
    task: Task,
    state_result: StateResult,
    user_settings=None,
) -> InterventionDecision:
    """
    Decide what intervention to make for a task given its current state.

    Rules:
    - COMPLETED / DROPPED → no action (terminal)
    - STALLED → send escalation once (no_more_action flag prevents repeats)
    - AT_RISK_2 → urgent poll if cooldown cleared
    - AT_RISK_1 → status poll if cooldown cleared
    - NOT_STARTED → start reminder when start_time is reached
    - ACTIVE → no action

    Args:
        task:          The Task ORM object.
        state_result:  Output from infer_state().
        user_settings: Optional UserSettings (uses global defaults if None).

    Returns:
        InterventionDecision with the chosen action.
    """
    state = state_result.state

    # ── Terminal states ────────────────────────────────────────────────────
    if state in (TaskState.COMPLETED, TaskState.DROPPED):
        return InterventionDecision(
            action=ActionType.NO_ACTION,
            reason=f"Task is in terminal state: {state}",
            task_id=str(task.id),
        )

    # ── STALLED — one-time escalation ─────────────────────────────────────
    if state == TaskState.STALLED:
        if task.no_more_action:
            return InterventionDecision(
                action=ActionType.NO_ACTION,
                reason="Escalation already sent for stalled task",
                task_id=str(task.id),
            )
        return InterventionDecision(
            action=ActionType.SEND_ESCALATION,
            reason=state_result.reason,
            task_id=str(task.id),
        )

    # ── Cooldown check ─────────────────────────────────────────────────────
    cooldown_minutes = (
        user_settings.nudge_cooldown_minutes
        if user_settings
        else settings.nudge_cooldown_minutes
    )
    if _is_on_cooldown(task, cooldown_minutes):
        return InterventionDecision(
            action=ActionType.NO_ACTION,
            reason=f"Within cooldown window ({cooldown_minutes} min)",
            task_id=str(task.id),
        )

    # ── AT_RISK states ─────────────────────────────────────────────────────
    if state == TaskState.AT_RISK:
        if state_result.trigger == "AT_RISK_2":
            return InterventionDecision(
                action=ActionType.SEND_URGENT_POLL,
                reason=state_result.reason,
                task_id=str(task.id),
            )
        if state_result.trigger == "AT_RISK_1":
            return InterventionDecision(
                action=ActionType.SEND_STATUS_POLL,
                reason=state_result.reason,
                task_id=str(task.id),
            )

    # ── NOT_STARTED — send start reminder ─────────────────────────────────
    if state == TaskState.NOT_STARTED:
        now = _now()
        start = task.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if now >= start:
            return InterventionDecision(
                action=ActionType.SEND_START_REMINDER,
                reason="Task start time has been reached",
                task_id=str(task.id),
            )

    # ── ACTIVE or before start ─────────────────────────────────────────────
    return InterventionDecision(
        action=ActionType.NO_ACTION,
        reason="Task is active — no intervention needed",
        task_id=str(task.id),
    )


def _is_on_cooldown(task: Task, cooldown_minutes: int) -> bool:
    """
    Check if the task is within the nudge cooldown window.
    Cooldown is measured from the last time an interaction was sent.
    """
    if task.last_response_time is None:
        return False
    now = _now()
    last = task.last_response_time
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) < timedelta(minutes=cooldown_minutes)
