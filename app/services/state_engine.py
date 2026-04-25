"""
State Inference Engine — CORE LOGIC.

Determines the current state of a task based purely on deterministic rules.
No LLM involvement. Inputs are timestamps, counters, and user responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog

from app.config import settings
from app.database.models import Task, TaskState

logger = structlog.get_logger(__name__)


@dataclass
class StateResult:
    """Output of the State Inference Engine."""
    state: TaskState
    trigger: str | None = None       # "AT_RISK_1" | "AT_RISK_2" | None
    pct_remaining: float | None = None
    reason: str = ""


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(seconds=offset_seconds)


def infer_state(task: Task, user_settings=None) -> StateResult:
    """
    Infer the current state of a task using percentage-based time thresholds.

    Priority order (highest to lowest):
    1. COMPLETED / DROPPED  — terminal, never re-evaluated
    2. STALLED              — max ignored prompts OR max extensions exceeded
    3. AT_RISK_2            — ≤10% time remaining
    4. AT_RISK_1            — ≤35% time remaining
    5. NOT_STARTED          — start_time not yet reached
    6. ACTIVE               — default case

    Args:
        task:          The Task ORM object.
        user_settings: Optional UserSettings object (uses global defaults if None).

    Returns:
        StateResult with the inferred state, trigger level, and reason.
    """
    offset = getattr(user_settings, "clock_offset_seconds", 0) or 0
    now = _now(offset)

    # ── 1. Terminal states — no re-evaluation ──────────────────────────────
    if task.state == TaskState.COMPLETED:
        return StateResult(state=TaskState.COMPLETED, reason="Task already completed")
    if task.state == TaskState.DROPPED:
        return StateResult(state=TaskState.DROPPED, reason="Task was dropped by user")

    # ── Resolve business rule limits ───────────────────────────────────────
    max_ignored = (
        user_settings.max_ignored_prompts
        if user_settings
        else settings.max_ignored_prompts
    )
    max_extensions = (
        user_settings.max_extensions
        if user_settings
        else settings.max_extensions
    )

    # ── 2. STALLED — exceeded intervention limits ──────────────────────────
    if task.ignored_prompts >= max_ignored:
        return StateResult(
            state=TaskState.STALLED,
            reason=f"Ignored {task.ignored_prompts}/{max_ignored} prompts",
        )
    if task.extensions_count >= max_extensions:
        return StateResult(
            state=TaskState.STALLED,
            reason=f"Exceeded max extensions ({task.extensions_count}/{max_extensions})",
        )

    # ── 3 & 4. AT_RISK — percentage-based thresholds ─────────────────────
    # Ensure both times are timezone-aware
    start = task.start_time.replace(tzinfo=timezone.utc) if task.start_time.tzinfo is None else task.start_time
    deadline = task.deadline.replace(tzinfo=timezone.utc) if task.deadline.tzinfo is None else task.deadline

    total_seconds = (deadline - start).total_seconds()
    remaining_seconds = (deadline - now).total_seconds()

    if total_seconds > 0:
        pct_remaining = (remaining_seconds / total_seconds) * 100
    else:
        pct_remaining = 0.0

    threshold_2 = settings.at_risk_threshold_2   # default 10%
    threshold_1 = settings.at_risk_threshold_1   # default 35%

    if remaining_seconds > 0:  # deadline not yet passed
        if pct_remaining <= threshold_2:
            return StateResult(
                state=TaskState.AT_RISK,
                trigger="AT_RISK_2",
                pct_remaining=round(pct_remaining, 1),
                reason=f"Only {pct_remaining:.1f}% of task time remains (≤{threshold_2}% threshold)",
            )
        if pct_remaining <= threshold_1:
            return StateResult(
                state=TaskState.AT_RISK,
                trigger="AT_RISK_1",
                pct_remaining=round(pct_remaining, 1),
                reason=f"{pct_remaining:.1f}% of task time remains (≤{threshold_1}% threshold)",
            )
    else:
        # Deadline has passed without completion
        return StateResult(
            state=TaskState.STALLED,
            reason="Deadline has passed without completion",
        )

    # ── 5. NOT_STARTED ────────────────────────────────────────────────────
    if now < start:
        return StateResult(
            state=TaskState.NOT_STARTED,
            reason="Task has not started yet",
        )

    # ── 6. ACTIVE — default ───────────────────────────────────────────────
    return StateResult(
        state=TaskState.ACTIVE,
        pct_remaining=round(pct_remaining, 1),
        reason="Task is in progress",
    )


def should_transition(current_state: TaskState, new_state: TaskState) -> bool:
    """
    Guard: determine if a state transition is valid.
    Terminal states (COMPLETED, DROPPED) cannot be left.
    """
    terminal = {TaskState.COMPLETED, TaskState.DROPPED}
    if current_state in terminal:
        return False
    return current_state != new_state
