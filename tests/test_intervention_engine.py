"""
Unit tests for the Intervention Engine.
"""

from unittest.mock import patch
from datetime import timedelta

from app.database.models import TaskState
from app.services.state_engine import StateResult
from app.services.intervention_engine import decide_intervention, ActionType


@patch("app.services.intervention_engine._now")
def test_terminal_states_no_action(mock_now_func, base_task, default_settings, mock_now):
    """Terminal states should never trigger interventions."""
    mock_now_func.return_value = mock_now
    
    for state in [TaskState.COMPLETED, TaskState.DROPPED]:
        state_result = StateResult(state=state)
        decision = decide_intervention(base_task, state_result, default_settings)
        assert decision.action == ActionType.NO_ACTION


@patch("app.services.intervention_engine._now")
def test_stalled_escalation(mock_now_func, base_task, default_settings, mock_now):
    """Stalled tasks should trigger a SEND_ESCALATION action once."""
    mock_now_func.return_value = mock_now
    state_result = StateResult(state=TaskState.STALLED, reason="Ignored too many prompts")

    # First time
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.SEND_ESCALATION

    # If already escalated (no_more_action flag is True), do nothing
    base_task.no_more_action = True
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.NO_ACTION


@patch("app.services.intervention_engine._now")
def test_cooldown_enforcement(mock_now_func, base_task, default_settings, mock_now):
    """Tasks should not trigger interventions if within the cooldown window."""
    mock_now_func.return_value = mock_now
    state_result = StateResult(state=TaskState.AT_RISK, trigger="AT_RISK_1")
    
    # Send poll immediately (no last response time)
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.SEND_STATUS_POLL

    # Set last response to 10 minutes ago (within 30 min cooldown)
    base_task.last_response_time = mock_now - timedelta(minutes=10)
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.NO_ACTION


@patch("app.services.intervention_engine._now")
def test_at_risk_actions(mock_now_func, base_task, default_settings, mock_now):
    """AT_RISK_1 -> Status Poll, AT_RISK_2 -> Urgent Poll."""
    mock_now_func.return_value = mock_now
    
    state_result = StateResult(state=TaskState.AT_RISK, trigger="AT_RISK_1")
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.SEND_STATUS_POLL

    state_result = StateResult(state=TaskState.AT_RISK, trigger="AT_RISK_2")
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.SEND_URGENT_POLL


@patch("app.services.intervention_engine._now")
def test_start_reminder(mock_now_func, base_task, default_settings, mock_now):
    """Start reminder should trigger once the start_time is reached."""
    state_result = StateResult(state=TaskState.NOT_STARTED)

    # 1. Before start time (09:00 < 10:00) -> NO_ACTION
    mock_now_func.return_value = mock_now - timedelta(hours=3)
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.NO_ACTION

    # 2. Start time reached (10:01 > 10:00) -> SEND_START_REMINDER
    mock_now_func.return_value = mock_now - timedelta(hours=1, minutes=59)
    decision = decide_intervention(base_task, state_result, default_settings)
    assert decision.action == ActionType.SEND_START_REMINDER
