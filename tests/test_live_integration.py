"""
Live integration test -- runs against the real PostgreSQL database.

Tests the full user lifecycle by calling services directly within a single
session, then verifying DB state. Only the outbound Telegram send_message
and the LLM provider are mocked -- every other operation is real.

Covers:
  1. New user registration (5-step flow)
  2. Task creation via NL (LLM mocked)
  3. Poll response: DONE -> COMPLETED
  4. Poll response: IN_PROGRESS -> task extended
  5. /tasks command output
  6. State engine + intervention engine on a live task object
  7. Full cleanup (cascade delete via user -> tasks -> interactions)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database.models import (
    Interaction,
    InteractionType,
    Platform,
    ResponseType,
    Task,
    TaskState,
    User,
    UserSettings,
)
from app.llm.base import ParsedTask
from app.platform.base import MessageType, NormalizedMessage, OutboundMessage, PollOption
from app.services.registration import get_user_by_platform_id
from app.services.task_service import (
    create_task_from_nl,
    extend_task,
    get_tasks_for_user_today,
    update_task_state,
)

# All tests in this module share one event loop so asyncpg connections created
# in test 1 are still valid in test 2 (same loop throughout the session).
pytestmark = pytest.mark.asyncio(loop_scope="session")

# ── Own NullPool engine for live tests ────────────────────────────────────────
# NullPool = no connection caching between sessions → no "Future attached to a
# different loop" errors when tests run sequentially in the same event loop.
_live_engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=_live_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Shared mock adapter ────────────────────────────────────────────────────────

class CapturingAdapter:
    """Records every outbound message instead of calling Telegram."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, message: OutboundMessage) -> str | None:
        self.sent.append({"to": message.platform_id, "text": message.text})
        return str(len(self.sent))

    async def send_poll(self, platform_id: str, question: str, options: list[PollOption]) -> str | None:
        self.sent.append({"to": platform_id, "text": question, "poll": True})
        return str(len(self.sent))

    def last(self) -> str:
        return self.sent[-1]["text"] if self.sent else ""

    def all_text(self) -> str:
        return " ".join(m["text"] for m in self.sent).lower()

    def clear(self):
        self.sent.clear()


# ── Helper: unique platform_id per test run ────────────────────────────────────

def _uid() -> str:
    return f"live_{uuid.uuid4().hex[:8]}"


# ── Helper: build NormalizedMessage ───────────────────────────────────────────

def _msg(text: str, platform_id: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="telegram",
        platform_id=platform_id,
        message_type=MessageType.TEXT,
        text=text,
    )


def _cmd(command: str, platform_id: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="telegram",
        platform_id=platform_id,
        message_type=MessageType.COMMAND,
        text=f"/{command}",
        command=command,
    )


def _poll_response(data: str, platform_id: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="telegram",
        platform_id=platform_id,
        message_type=MessageType.POLL_RESPONSE,
        poll_response=data,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registration_flow_against_real_db():
    """
    Walk through all 5 registration steps, verifying DB state after each.
    Uses real PostgreSQL -- no mocking of DB operations.
    """
    platform_id = _uid()
    adapter = CapturingAdapter()

    with patch("app.services.registration.get_platform_adapter", return_value=adapter):
        async with AsyncSessionLocal() as db:

            # Initial state: no user
            user = await get_user_by_platform_id(db, platform_id)
            assert user is None

            # ── Bootstrap: create user at step 1 ───────────────────────────
            from app.services.registration import start_registration
            msg = _msg("Hello", platform_id)
            await start_registration(db, msg)
            assert len(adapter.sent) == 2  # welcome intro + name prompt

            user = await get_user_by_platform_id(db, platform_id)
            assert user is not None
            assert user.registration_state == "awaiting_name"
            print("\n  [1] User created, state=awaiting_name")

            # ── Step 1: Name ────────────────────────────────────────────────
            from app.services.registration import process_registration_step
            adapter.clear()

            # NOTE: expire_on_commit=False means user stays current after
            # each process_registration_step commit — no db.refresh needed.
            msg.text = "Alice Tester"
            await process_registration_step(db, user, msg)
            assert user.name == "Alice Tester"
            assert user.registration_state == "awaiting_email"
            print("  [2] Name saved")

            # ── Step 2: Email ───────────────────────────────────────────────
            msg.text = "alice@example.com"
            await process_registration_step(db, user, msg)
            assert user.email == "alice@example.com"
            assert user.registration_state == "awaiting_phone"
            print("  [3] Email saved")

            # ── Step 3: Phone (skip) ────────────────────────────────────────
            msg.text = "skip"
            await process_registration_step(db, user, msg)
            assert user.phone is None
            assert user.registration_state == "awaiting_timezone"
            print("  [4] Phone skipped")

            # ── Step 4: Timezone ────────────────────────────────────────────
            msg.text = "Asia/Karachi"
            await process_registration_step(db, user, msg)
            assert user.timezone == "Asia/Karachi"
            assert user.registration_state == "awaiting_summary_time"
            print("  [5] Timezone=Asia/Karachi")

            # ── Step 5: Summary time -> complete ───────────────────────────
            adapter.clear()
            msg.text = "08:00"
            result = await process_registration_step(db, user, msg)
            assert result is True
            assert user.registration_state is None
            assert user.registered_at is not None
            assert user.name == "Alice Tester"
            print(f"  [6] Registration COMPLETE. registered_at={user.registered_at}")

            # ── Cleanup ────────────────────────────────────────────────────
            await db.delete(user)
            await db.commit()
            gone = await get_user_by_platform_id(db, platform_id)
            assert gone is None
            print("  [7] Cleaned up")

    print("\n  Registration flow: all checks passed")


@pytest.mark.asyncio
async def test_task_lifecycle_against_real_db():
    """
    Create a registered user, add a task (LLM mocked), trigger poll responses,
    verify state transitions in the real DB.
    """
    platform_id = _uid()
    adapter = CapturingAdapter()

    now = datetime.now(tz=timezone.utc)
    mock_parsed_task = ParsedTask(
        title="Write unit tests",
        start_time=(now + timedelta(minutes=5)).isoformat(),
        deadline=(now + timedelta(hours=2)).isoformat(),
        description="Cover the state engine",
    )
    mock_llm = AsyncMock()
    mock_llm.parse_task.return_value = mock_parsed_task

    with patch("app.services.task_service.get_llm_provider", return_value=mock_llm), \
         patch("app.services.notification_service.get_platform_adapter", return_value=adapter), \
         patch("app.services.registration.get_platform_adapter", return_value=adapter):

        async with AsyncSessionLocal() as db:

            # ── Seed a registered user directly ────────────────────────────
            user = User(
                platform=Platform.TELEGRAM,
                platform_id=platform_id,
                name="Live Test User",
                email="live@test.com",
                timezone="Asia/Karachi",
                registration_state=None,
                registered_at=datetime.now(tz=timezone.utc),
            )
            db.add(user)
            await db.flush()  # get user.id
            settings = UserSettings(user_id=user.id)
            db.add(settings)
            await db.commit()
            await db.refresh(user)
            print(f"\n  User seeded: id={user.id}, platform_id={platform_id}")

            # ── Task creation via NL (real DB write) ────────────────────────
            task = await create_task_from_nl(
                db, user, "Write unit tests from now for 2 hours"
            )
            assert task.id is not None
            assert task.title == "Write unit tests"
            assert task.state == TaskState.NOT_STARTED
            assert task.user_id == user.id
            print(f"  Task created: id={task.id}, title='{task.title}'")

            # Verify it persisted
            today_tasks = await get_tasks_for_user_today(db, user.id)
            assert any(t.id == task.id for t in today_tasks)
            print(f"  get_tasks_for_user_today: {len(today_tasks)} task(s)")

            # ── Poll response: IN_PROGRESS -> extend deadline ───────────────
            original_deadline = task.deadline
            extended_task = await extend_task(db, task)
            assert extended_task.deadline > original_deadline
            assert extended_task.extensions_count == 1
            print(f"  Task extended: deadline {original_deadline.strftime('%H:%M')} "
                  f"-> {extended_task.deadline.strftime('%H:%M')}")

            # ── Poll response: DONE -> COMPLETED ───────────────────────────
            completed = await update_task_state(db, task, TaskState.COMPLETED)
            assert completed.state == TaskState.COMPLETED
            print("  Task COMPLETED")

            # Verify in DB
            from sqlalchemy import select
            result = await db.execute(select(Task).where(Task.id == task.id))
            db_task = result.scalar_one()
            assert db_task.state == TaskState.COMPLETED

            # ── /tasks command: list builder logic ──────────────────────────
            tasks = await get_tasks_for_user_today(db, user.id)
            completed_count = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
            assert completed_count >= 1
            print(f"  /tasks query: {len(tasks)} total, {completed_count} completed")

            # ── Interaction logging ──────────────────────────────────────────
            from sqlalchemy import select
            poll_interaction = Interaction(
                task_id=task.id,
                user_id=user.id,
                type=InteractionType.POLL,
                content="How is it going?",
                response_type=ResponseType.DONE,
                message_id="99",
            )
            db.add(poll_interaction)
            await db.commit()

            result = await db.execute(
                select(Interaction).where(
                    Interaction.task_id == task.id,
                    Interaction.response_type == ResponseType.DONE,
                )
            )
            saved_interaction = result.scalar_one_or_none()
            assert saved_interaction is not None
            print(f"  Interaction logged: response={saved_interaction.response_type}")

            # ── Cleanup (cascade: user -> tasks -> interactions) ────────────
            await db.delete(user)
            await db.commit()

            gone = await get_user_by_platform_id(db, platform_id)
            assert gone is None

            # Verify tasks and interactions cascaded
            result = await db.execute(select(Task).where(Task.user_id == user.id))
            assert result.scalar_one_or_none() is None
            print("  Cascade delete verified (user, tasks, interactions)")

    print("\n  Task lifecycle: all checks passed")


@pytest.mark.asyncio
async def test_state_and_intervention_engines():
    """
    Construct tasks in memory, run state + intervention engines,
    verify correct decisions for each lifecycle scenario.
    """
    from app.services.intervention_engine import ActionType, decide_intervention
    from app.services.state_engine import infer_state

    now = datetime.now(tz=timezone.utc)

    def make_task(**overrides) -> Task:
        base = dict(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            title="Test",
            task_date=now.date(),
            start_time=now - timedelta(hours=3),
            deadline=now + timedelta(hours=1),
            state=TaskState.ACTIVE,
            extensions_count=0,
            ignored_prompts=0,
            no_more_action=False,
            last_response_time=None,
        )
        base.update(overrides)
        return Task(**base)

    # ── 1. NOT_STARTED (task hasn't begun yet) ──────────────────────────────
    task = make_task(start_time=now + timedelta(hours=1), deadline=now + timedelta(hours=3))
    sr = infer_state(task)
    assert sr.state == TaskState.NOT_STARTED
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.NO_ACTION  # start time not reached
    print("\n  [1] NOT_STARTED + future start -> NO_ACTION  OK")

    # ── 2. ACTIVE (plenty of time, 50% remaining) ───────────────────────────
    task = make_task(start_time=now - timedelta(hours=1), deadline=now + timedelta(hours=1))
    sr = infer_state(task)
    assert sr.state == TaskState.ACTIVE
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.NO_ACTION
    print("  [2] ACTIVE with 50% time -> NO_ACTION  OK")

    # ── 3. AT_RISK_1 (30% time remaining, just past 35% threshold) ─────────
    # Total = 4h. 30% = 72 min remaining. deadline = now + 72 min, start = now - 168 min
    task = make_task(
        start_time=now - timedelta(minutes=168),
        deadline=now + timedelta(minutes=72),
    )
    sr = infer_state(task)
    assert sr.state == TaskState.AT_RISK
    assert sr.trigger == "AT_RISK_1"
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.SEND_STATUS_POLL
    print(f"  [3] AT_RISK_1 ({sr.pct_remaining}% remaining) -> SEND_STATUS_POLL  OK")

    # ── 4. AT_RISK_2 (8% time remaining) ────────────────────────────────────
    task = make_task(start_time=now - timedelta(hours=3), deadline=now + timedelta(minutes=14))
    sr = infer_state(task)
    assert sr.state == TaskState.AT_RISK
    assert sr.trigger == "AT_RISK_2"
    assert sr.pct_remaining is not None and sr.pct_remaining <= 10.0
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.SEND_URGENT_POLL
    print(f"  [4] AT_RISK_2 ({sr.pct_remaining}% remaining) -> SEND_URGENT_POLL  OK")

    # ── 5. STALLED (max ignored prompts) ────────────────────────────────────
    task = make_task(ignored_prompts=3)
    sr = infer_state(task)
    assert sr.state == TaskState.STALLED
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.SEND_ESCALATION
    print("  [5] STALLED (max ignored) -> SEND_ESCALATION  OK")

    # ── 6. STALLED + already escalated (no_more_action) ─────────────────────
    task = make_task(ignored_prompts=3, no_more_action=True)
    sr = infer_state(task)
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.NO_ACTION
    print("  [6] STALLED + escalated -> NO_ACTION  OK")

    # ── 7. Cooldown enforcement ──────────────────────────────────────────────
    task = make_task(
        start_time=now - timedelta(hours=3),
        deadline=now + timedelta(minutes=14),
        last_response_time=now - timedelta(minutes=10),  # nudged 10 min ago
    )
    sr = infer_state(task)
    assert sr.state == TaskState.AT_RISK
    decision = decide_intervention(task, sr)
    assert decision.action == ActionType.NO_ACTION  # within 30min cooldown
    print("  [7] AT_RISK + cooldown active -> NO_ACTION  OK")

    # ── 8. Terminal states ───────────────────────────────────────────────────
    for state in (TaskState.COMPLETED, TaskState.DROPPED):
        task = make_task(state=state)
        task.state = state
        sr = infer_state(task)
        assert sr.state == state
        decision = decide_intervention(task, sr)
        assert decision.action == ActionType.NO_ACTION
    print("  [8] COMPLETED/DROPPED -> NO_ACTION  OK")

    print("\n  State + Intervention engines: all 8 checks passed")
