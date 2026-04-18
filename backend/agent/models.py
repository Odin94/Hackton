"""SQLAlchemy ORM models for the agent layer.

Entities
--------
User            — platform user
ScheduleEvent   — lecture / tutorium / study session
Quiz            — generated quiz linked to one user and optionally many events
Notification    — delivery record; dispatched via WebSocket when due
QuizResult      — user's performance on a completed quiz
ChatMessage     — persisted per-user chat history
AgentLog        — internal log written by the background LLM agent
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


class _Timestamps:
    """Mixin that adds ISO-8601 createdAt / updatedAt columns to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


# ---------------------------------------------------------------------------
# Association table — Quiz ↔ ScheduleEvent  (many-to-many)
# ---------------------------------------------------------------------------

quiz_schedule_events = Table(
    "quiz_schedule_events",
    Base.metadata,
    Column("quiz_id", ForeignKey("quizzes.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "schedule_event_id",
        ForeignKey("schedule_events.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class User(_Timestamps, Base):
    """Platform user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Primary identifier used for login — must be unique.
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Optional profile fields; not required for signup.
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), unique=True, index=True, nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    schedule_events: Mapped[list["ScheduleEvent"]] = relationship(back_populates="user")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="user")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")
    quiz_results: Mapped[list["QuizResult"]] = relationship(back_populates="user")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user")


class ScheduleEvent(_Timestamps, Base):
    """A timed calendar event belonging to one user."""

    __tablename__ = "schedule_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "lecture" | "tutorium" | "study session"
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="schedule_events")
    quizzes: Mapped[list["Quiz"]] = relationship(
        secondary=quiz_schedule_events, back_populates="schedule_events"
    )


class Quiz(_Timestamps, Base):
    """A generated quiz.

    ``questions`` is a JSON list whose items follow the cognee QuizItem shape::

        [{"question": str, "answer": str, "topic": str, "source_ref": str | None}, ...]
    """

    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    topic: Mapped[str] = mapped_column(String(256), nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Serialized list[QuizItem] — stored as JSON so the schema can evolve freely.
    questions: Mapped[list] = mapped_column(JSON, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="quizzes")
    schedule_events: Mapped[list["ScheduleEvent"]] = relationship(
        secondary=quiz_schedule_events, back_populates="quizzes"
    )
    notifications: Mapped[list["Notification"]] = relationship(back_populates="quiz")
    results: Mapped[list["QuizResult"]] = relationship(back_populates="quiz")


class Notification(_Timestamps, Base):
    """A pending or delivered notification for a user, optionally carrying a quiz."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "pending" | "complete" | "canceled"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    target_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    content: Mapped[str] = mapped_column(String(2048), nullable=False)
    quiz_id: Mapped[int | None] = mapped_column(
        ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="notifications")
    quiz: Mapped["Quiz | None"] = relationship(back_populates="notifications")


class QuizResult(_Timestamps, Base):
    """Record of a user completing a quiz."""

    __tablename__ = "quiz_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    correct_answers: Mapped[int] = mapped_column(Integer, nullable=False)
    false_answers: Mapped[int] = mapped_column(Integer, nullable=False)
    quiz_taken_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="quiz_results")
    quiz: Mapped["Quiz"] = relationship(back_populates="results")


class ChatMessage(Base):
    """Persisted chat history for one user conversation stream."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("author IN ('user', 'system')", name="ck_chat_messages_author"),
        UniqueConstraint("user_id", "sequence_number", name="uq_chat_messages_user_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, default=_now
    )
    author: Mapped[str] = mapped_column(String(16), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped["User"] = relationship(back_populates="chat_messages")


class AgentLog(_Timestamps, Base):
    """Entries written by the background LLM agent via the write_to_db tool."""

    __tablename__ = "agent_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
