"""SQLAlchemy ORM models for the agent layer.

Entities
--------
User            — platform user
Course          — user-owned course catalog entry
Deadline        — dated milestone/exam tied to a course
ScheduleEvent   — lecture / tutorium / study session
Quiz            — generated quiz linked to one user and optionally many events
Notification    — delivery record; dispatched via WebSocket when due
QuizResult      — user's performance on a completed quiz
ChatMessage     — persisted per-user chat history
AgentLog        — internal log written by the background LLM agent
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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
    courses: Mapped[list["Course"]] = relationship(back_populates="user")
    deadlines: Mapped[list["Deadline"]] = relationship(back_populates="user")
    schedule_events: Mapped[list["ScheduleEvent"]] = relationship(back_populates="user")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="user")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")
    quiz_results: Mapped[list["QuizResult"]] = relationship(back_populates="user")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user")
    demo_conversation_state: Mapped["DemoConversationState | None"] = relationship(
        back_populates="user",
        uselist=False,
    )


class Course(_Timestamps, Base):
    """A user-owned course the app can link schedules, deadlines, and quizzes to."""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_courses_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    user: Mapped["User"] = relationship(back_populates="courses")
    deadlines: Mapped[list["Deadline"]] = relationship(back_populates="course")
    schedule_events: Mapped[list["ScheduleEvent"]] = relationship(back_populates="course")
    quizzes: Mapped[list["Quiz"]] = relationship(back_populates="course")


class Deadline(_Timestamps, Base):
    """A course-linked deadline, exam date, or other dated milestone."""

    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    user: Mapped["User"] = relationship(back_populates="deadlines")
    course: Mapped["Course"] = relationship(back_populates="deadlines")


class ScheduleEvent(_Timestamps, Base):
    """A timed calendar event belonging to one user."""

    __tablename__ = "schedule_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # "lecture" | "tutorium" | "study session"
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="schedule_events")
    course: Mapped["Course | None"] = relationship(back_populates="schedule_events")
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
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    topic: Mapped[str] = mapped_column(String(256), nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Serialized list[QuizItem] — stored as JSON so the schema can evolve freely.
    questions: Mapped[list] = mapped_column(JSON, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="quizzes")
    course: Mapped["Course | None"] = relationship(back_populates="quizzes")
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


class DemoConversationState(_Timestamps, Base):
    """Small persisted state machine used for the scripted demo chat flow."""

    __tablename__ = "demo_conversation_states"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_demo_conversation_states_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    course_name: Mapped[str] = mapped_column(String(256), nullable=False)
    coverage_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiz_id: Mapped[int | None] = mapped_column(
        ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True
    )
    average_score_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=70)

    user: Mapped["User"] = relationship(back_populates="demo_conversation_state")
    quiz: Mapped["Quiz | None"] = relationship()


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
