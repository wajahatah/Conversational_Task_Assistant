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
    SEND_STATUS_POLL = "send_status_poll"       # AT_RISK_1 — first nudge
    SEND_URGENT_POLL = "send_urgent_poll"        # AT_RISK_2 — second nudge
    SEND_ESCALATION = "send_escalation"          # STALLED — no extension done
    SEND_FINAL_POLL = "send_final_poll"          # STALLED — after extension, ask DONE/STALL
    NO_ACTION = "no_action"


@dataclass
class InterventionDecision:
    """Output of the Intervention Engine."""
    action: ActionType
    reason: str
    task_id: str | None = None


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(seconds=offset_seconds)


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
    offset = getattr(user_settings, "clock_offset_seconds", 0) or 0

    # ── Terminal states ────────────────────────────────────────────────────
    if state in (TaskState.COMPLETED, TaskState.DROPPED):
        return InterventionDecision(
            action=ActionType.NO_ACTION,
            reason=f"Task is in terminal state: {state}",
            task_id=str(task.id),
        )

    # ── STALLED — one-time notification ───────────────────────────────────
    if state == TaskState.STALLED:
        if task.no_more_action:
            return InterventionDecision(
                action=ActionType.NO_ACTION,
                reason="Already notified for stalled task",
                task_id=str(task.id),
            )
        # Task was extended: deadline passed after extension → ask DONE or STALL
        if task.extensions_count > 0:
            return InterventionDecision(
                action=ActionType.SEND_FINAL_POLL,
                reason=state_result.reason,
                task_id=str(task.id),
            )
        # No extension was used: deadline passed without responses → auto-stall
        return InterventionDecision(
            action=ActionType.SEND_ESCALATION,
            reason=state_result.reason,
            task_id=str(task.id),
        )

    # ── START REMINDER — fires once when task crosses its start time ──────────
    # task.state is the stored DB state (NOT_STARTED = never started yet).
    # state_result.state is freshly inferred from the clock.
    # When the stored state is NOT_STARTED but inferred state is ACTIVE or
    # AT_RISK, the start time has been reached this cycle → send the reminder
    # before the state is persisted. Bypasses cooldown: this fires exactly once.
    if task.state == TaskState.NOT_STARTED and state in (TaskState.ACTIVE, TaskState.AT_RISK):
        return InterventionDecision(
            action=ActionType.SEND_START_REMINDER,
            reason="Task start time has been reached",
            task_id=str(task.id),
        )

    # ── AT_RISK_2 — urgent poll evaluated before the standard cooldown ────────
    # The gap between AT_RISK_1 (35%) and AT_RISK_2 (10%) is 25% of task
    # duration.  For short tasks this gap is smaller than the 30-min cooldown,
    # which would suppress the urgent nudge entirely.  We use a proportional
    # mini-cooldown (10% of task duration, min 2 min) so AT_RISK_2 always fires.
    if state == TaskState.AT_RISK and state_result.trigger == "AT_RISK_2":
        task_duration_min = (task.deadline - task.start_time).total_seconds() / 60
        mini_cooldown = max(2, int(task_duration_min * 0.10))
        if not _is_on_cooldown(task, mini_cooldown, offset):
            return InterventionDecision(
                action=ActionType.SEND_URGENT_POLL,
                reason=state_result.reason,
                task_id=str(task.id),
            )
        return InterventionDecision(
            action=ActionType.NO_ACTION,
            reason="Urgent poll already sent — waiting for response or deadline",
            task_id=str(task.id),
        )

    # ── AT_RISK_1 — first nudge, evaluated with a proportional mini-cooldown
    # The gap between the start reminder and AT_RISK_1 is 65% of task duration.
    # The default 30-min standard cooldown blocks AT_RISK_1 for any task shorter
    # than ~46 minutes.  We use 10% of task duration (min 2 min) so AT_RISK_1
    # always fires regardless of task length.
    if state == TaskState.AT_RISK and state_result.trigger == "AT_RISK_1":
        task_duration_min = (task.deadline - task.start_time).total_seconds() / 60
        mini_cooldown = max(2, int(task_duration_min * 0.10))
        if not _is_on_cooldown(task, mini_cooldown, offset):
            return InterventionDecision(
                action=ActionType.SEND_STATUS_POLL,
                reason=state_result.reason,
                task_id=str(task.id),
            )
        return InterventionDecision(
            action=ActionType.NO_ACTION,
            reason="Status poll already sent — waiting for response",
            task_id=str(task.id),
        )

    # ── Standard cooldown (currently unused — kept for future nudge types) ─
    cooldown_minutes = (
        user_settings.nudge_cooldown_minutes
        if user_settings
        else settings.nudge_cooldown_minutes
    )
    if _is_on_cooldown(task, cooldown_minutes, offset):
        return InterventionDecision(
            action=ActionType.NO_ACTION,
            reason=f"Within cooldown window ({cooldown_minutes} min)",
            task_id=str(task.id),
        )

    # ── ACTIVE or before start ─────────────────────────────────────────────
    return InterventionDecision(
        action=ActionType.NO_ACTION,
        reason="Task is active — no intervention needed",
        task_id=str(task.id),
    )


def _is_on_cooldown(task: Task, cooldown_minutes: int, offset_seconds: int = 0) -> bool:
    """
    Check if the task is within the nudge cooldown window.
    Cooldown is measured from the last time an interaction was sent.
    """
    if task.last_response_time is None:
        return False
    now = _now(offset_seconds)
    last = task.last_response_time
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) < timedelta(minutes=cooldown_minutes)
