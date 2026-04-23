from __future__ import annotations

import enum
import uuid
from datetime import datetime, time, date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ─── Base ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Enums ────────────────────────────────────────────────────────────────────

class Platform(str, enum.Enum):
    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"


class TaskState(str, enum.Enum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    AT_RISK = "at_risk"
    STALLED = "stalled"
    COMPLETED = "completed"
    DROPPED = "dropped"


class InteractionType(str, enum.Enum):
    POLL = "poll"
    REMINDER = "reminder"
    MESSAGE = "message"
    SYSTEM = "system"


class ResponseType(str, enum.Enum):
    DONE = "done"
    IN_PROGRESS = "in_progress"
    DROP = "drop"
    NEED_HELP = "need_help"


class ConversationRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


# ─── Models ───────────────────────────────────────────────────────────────────

class User(Base):
    """Registered user — one record per unique platform_id."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False)
    platform_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    summary_trigger_time: Mapped[time] = mapped_column(Time, default=time(8, 0))

    # Tracks multi-step onboarding progress; null = registration complete
    registration_state: Mapped[str | None] = mapped_column(String(50), default="awaiting_name")

    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ──
    # cascade="all, delete-orphan": deleting a User deletes all child rows via
    # the ORM (works even when children are already loaded via selectin).
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="user", lazy="selectin",
        cascade="all, delete-orphan",
    )
    interactions: Mapped[list["Interaction"]] = relationship(
        "Interaction", back_populates="user",
        cascade="all, delete-orphan",
    )
    conversation_history: Mapped[list["ConversationHistory"]] = relationship(
        "ConversationHistory", back_populates="user",
        cascade="all, delete-orphan",
    )
    settings: Mapped["UserSettings"] = relationship(
        "UserSettings", back_populates="user", uselist=False, lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User platform_id={self.platform_id} name={self.name}>"


class Task(Base):
    """A single daily task belonging to a user."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Scopes the task to a specific calendar date (one-day validity)
    task_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    state: Mapped[TaskState] = mapped_column(
        Enum(TaskState), default=TaskState.NOT_STARTED, nullable=False, index=True
    )
    extensions_count: Mapped[int] = mapped_column(Integer, default=0)
    ignored_prompts: Mapped[int] = mapped_column(Integer, default=0)
    last_response_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Set to True after STALLED escalation is sent — prevents repeat escalation
    no_more_action: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ──
    user: Mapped["User"] = relationship("User", back_populates="tasks")
    interactions: Mapped[list["Interaction"]] = relationship(
        "Interaction", back_populates="task", cascade="all, delete-orphan"
    )
    conversation_history: Mapped[list["ConversationHistory"]] = relationship(
        "ConversationHistory", back_populates="task"
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} title={self.title!r} state={self.state}>"


class Interaction(Base):
    """
    Logs every bot-initiated action (poll, reminder, message)
    and the user's response to it.
    """

    __tablename__ = "interactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[InteractionType] = mapped_column(Enum(InteractionType), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)

    # Null until the user responds
    response_type: Mapped[ResponseType | None] = mapped_column(Enum(ResponseType))

    # Platform message ID — used to edit/delete polls on Telegram
    message_id: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ──
    task: Mapped["Task"] = relationship("Task", back_populates="interactions")
    user: Mapped["User"] = relationship("User", back_populates="interactions")

    def __repr__(self) -> str:
        return f"<Interaction type={self.type} response={self.response_type}>"


class ConversationHistory(Base):
    """
    Stores multi-turn LLM conversation messages.
    task_id is nullable — for general (non-task-specific) conversations.
    """

    __tablename__ = "conversation_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    role: Mapped[ConversationRole] = mapped_column(Enum(ConversationRole), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ──
    user: Mapped["User"] = relationship("User", back_populates="conversation_history")
    task: Mapped["Task | None"] = relationship("Task", back_populates="conversation_history")

    def __repr__(self) -> str:
        return f"<ConversationHistory role={self.role} user_id={self.user_id}>"


class UserSettings(Base):
    """Per-user configurable settings. Created during registration."""

    __tablename__ = "user_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    summary_trigger_time: Mapped[time] = mapped_column(Time, default=time(8, 0))
    max_extensions: Mapped[int] = mapped_column(Integer, default=2)
    max_ignored_prompts: Mapped[int] = mapped_column(Integer, default=3)
    nudge_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ── Relationships ──
    user: Mapped["User"] = relationship("User", back_populates="settings")

    def __repr__(self) -> str:
        return f"<UserSettings user_id={self.user_id}>"
