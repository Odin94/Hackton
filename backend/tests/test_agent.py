"""Tests for the agent layer: models, db helpers, quiz workflow, notification dispatch.

All tests use an in-memory SQLite database so they never touch the real
agent_memory.db file.  Cognee and litellm calls are mocked at the module
boundary.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.database import Base
from agent.db import create_notification, list_user_ids, read_recent, write_entry
from agent.models import (
    ChatMessage,
    Course,
    Deadline,
    Notification,
    Quiz,
    QuizResult,
    ScheduleEvent,
    User,
)
from agent.quiz_workflow import (
    _dispatch_due_notifications_impl,
    _generate_quizzes_impl,
)
from agent.scheduler import _build_llm_user_prompt, _build_scheduler_sqlite_context
from app.types import QuizItem

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    """In-memory async SQLite engine with all tables created."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    """Bare AsyncSession backed by the in-memory engine."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def patched_session_local(engine):
    """
    Patch agent.db.AsyncSessionLocal so that write_entry / read_recent use
    the in-memory engine instead of the real one.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch("agent.db.AsyncSessionLocal", factory):
        yield factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_user_counter = 0


async def _make_user(session: AsyncSession, **kw) -> User:
    global _user_counter
    _user_counter += 1
    user = User(
        username=kw.get("username", f"user_{_user_counter}"),
        name=kw.get("name"),
        email=kw.get("email"),
    )
    session.add(user)
    await session.flush()
    return user


def _past(minutes: int = 5) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _future(minutes: int = 5) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


async def test_create_user(session: AsyncSession):
    user = User(
        username="bob",
        name="Bob",
        email="bob@example.com",
        phone_number="+49123456789",
    )
    session.add(user)
    await session.commit()

    fetched = await session.get(User, user.id)
    assert fetched is not None
    assert fetched.username == "bob"
    assert fetched.email == "bob@example.com"
    assert fetched.phone_number == "+49123456789"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


# ---------------------------------------------------------------------------
# ScheduleEvent
# ---------------------------------------------------------------------------


async def test_create_schedule_event(session: AsyncSession):
    user = await _make_user(session)
    event = ScheduleEvent(
        user_id=user.id,
        type="lecture",
        name="Machine Learning",
        start_datetime=_past(60),
        end_datetime=_past(0),
    )
    session.add(event)
    await session.commit()

    fetched = await session.get(ScheduleEvent, event.id)
    assert fetched is not None
    assert fetched.type == "lecture"
    assert fetched.user_id == user.id


# ---------------------------------------------------------------------------
# QuizResult
# ---------------------------------------------------------------------------


async def test_create_quiz_result(session: AsyncSession):
    user = await _make_user(session)
    quiz = Quiz(
        user_id=user.id,
        title="ML Quiz",
        topic="Neural Networks",
        estimated_duration_minutes=10,
        questions=[{"question": "Q?", "answer": "A"}],
    )
    session.add(quiz)
    await session.flush()

    result = QuizResult(
        user_id=user.id,
        quiz_id=quiz.id,
        correct_answers=4,
        false_answers=1,
        quiz_taken_datetime=datetime.now(UTC),
    )
    session.add(result)
    await session.commit()

    fetched = await session.get(QuizResult, result.id)
    assert fetched is not None
    assert fetched.correct_answers == 4
    assert fetched.false_answers == 1


# ---------------------------------------------------------------------------
# ChatMessage
# ---------------------------------------------------------------------------


async def test_create_chat_messages_for_user(session: AsyncSession):
    user = await _make_user(session)
    session.add_all(
        [
            ChatMessage(
                user_id=user.id,
                timestamp=_past(2),
                author="user",
                sequence_number=1,
                content="What quizzes do I have today?",
            ),
            ChatMessage(
                user_id=user.id,
                timestamp=_past(1),
                author="system",
                sequence_number=2,
                content="You have a machine learning quiz this afternoon.",
            ),
        ]
    )
    await session.commit()

    fetched = (
        (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.user_id == user.id)
                .order_by(ChatMessage.sequence_number.asc())
            )
        )
        .scalars()
        .all()
    )

    assert [message.author for message in fetched] == ["user", "system"]
    assert [message.sequence_number for message in fetched] == [1, 2]
    assert fetched[1].content == "You have a machine learning quiz this afternoon."


# ---------------------------------------------------------------------------
# AgentLog (db.write_entry / db.read_recent)
# ---------------------------------------------------------------------------


async def test_write_and_read_agent_log(engine):
    """write_entry and read_recent should round-trip through the in-memory DB."""
    factory = async_sessionmaker(engine, expire_on_commit=False)

    with patch("agent.db.AsyncSessionLocal", factory):
        row_id = await write_entry("quiz_insight", "Students struggle with backprop.")
        assert isinstance(row_id, int)

        rows = await read_recent(limit=5)

    assert len(rows) == 1
    assert rows[0]["entry_type"] == "quiz_insight"
    assert "backprop" in rows[0]["content"]


async def test_read_recent_returns_newest_first(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)

    with patch("agent.db.AsyncSessionLocal", factory):
        await write_entry("t", "first")
        await write_entry("t", "second")
        rows = await read_recent(limit=2)

    assert rows[0]["content"] == "second"
    assert rows[1]["content"] == "first"


async def test_list_user_ids_returns_sorted(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await _make_user(session, username="charlie")
        await _make_user(session, username="alice")
        await session.commit()

    with patch("agent.db.AsyncSessionLocal", factory):
        assert await list_user_ids() == [1, 2]


async def test_create_notification_dedupes_identical_pending(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    target = _future(90)

    async with factory() as session:
        user = await _make_user(session)
        await session.commit()

    with patch("agent.db.AsyncSessionLocal", factory):
        first_id = await create_notification(user.id, "Hydrate before class.", target)
        second_id = await create_notification(user.id, "Hydrate before class.", target)

    assert first_id == second_id

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(Notification).where(Notification.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


async def test_build_scheduler_sqlite_context_includes_user_state(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        user = await _make_user(session, username="planner", name="Planner")
        course = Course(user_id=user.id, name="Algorithms")
        session.add(course)
        await session.flush()
        event = ScheduleEvent(
            user_id=user.id,
            course_id=course.id,
            type="lecture",
            name="Algorithms",
            start_datetime=_future(120),
            end_datetime=_future(150),
        )
        notification = Notification(
            user_id=user.id,
            status="pending",
            target_datetime=_future(30),
            content="Review your notes before Algorithms.",
        )
        quiz = Quiz(
            user_id=user.id,
            course_id=course.id,
            title="Quiz: Algorithms",
            topic="Algorithms",
            estimated_duration_minutes=8,
            questions=[{"question": "Q?", "answer": "A"}],
        )
        deadline = Deadline(
            user_id=user.id,
            course_id=course.id,
            datetime=_future(240),
            name="Algorithms exam",
        )
        result = QuizResult(
            user_id=user.id,
            quiz=quiz,
            correct_answers=3,
            false_answers=1,
            quiz_taken_datetime=_past(120),
        )
        session.add_all([event, notification, quiz, deadline, result])
        await session.commit()

    with patch("agent.scheduler.AsyncSessionLocal", factory):
        context = await _build_scheduler_sqlite_context(user.id)

    assert "Algorithms" in context
    assert "Algorithms exam" in context
    assert "course_id=" in context
    assert "Review your notes before Algorithms." in context
    assert "quiz_id=" in context


def test_build_llm_user_prompt_includes_diary_and_sqlite_context():
    prompt = _build_llm_user_prompt(
        user_id=7,
        sqlite_context="Upcoming schedule events:\n- lecture: ML",
        cognee_context=(
            "Diary retrieval:\nThe student focuses better after a morning run.\n\n"
            "Materials retrieval:\nTransformers use attention."
        ),
        recent=[{"created_at": "2026-04-18T10:00:00+00:00", "entry_type": "study_pattern", "content": "Exercise seems to help focus."}],
    )

    assert "user 7" in prompt
    assert "morning run" in prompt
    assert "Transformers use attention" in prompt
    assert "Upcoming schedule events" in prompt
    assert "study_pattern" in prompt


async def test_build_scheduler_cognee_context_uses_combined_seeded_data():
    expected = "Diary retrieval:\nHabits\n\nMaterials retrieval:\nTopics"
    with patch("agent.scheduler.query_combined_context", new=AsyncMock(return_value=expected)) as mock_query:
        from agent.scheduler import _build_scheduler_cognee_context

        context = await _build_scheduler_cognee_context()

    assert context == expected
    kwargs = mock_query.await_args.kwargs
    assert "study habits" in kwargs["diary_query"]
    assert "course materials" in kwargs["materials_query"]


# ---------------------------------------------------------------------------
# Quiz workflow — generate_quizzes_for_user_events
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_quiz_items() -> list[QuizItem]:
    return [
        QuizItem(question="What is backprop?", answer="Gradient descent.", topic="ML"),
        QuizItem(question="Define overfitting.", answer="High variance.", topic="ML"),
    ]


async def test_generate_quizzes_creates_quiz_and_notification(
    session: AsyncSession, fake_quiz_items
):
    user = await _make_user(session)
    event = ScheduleEvent(
        user_id=user.id,
        type="lecture",
        name="Machine Learning",
        start_datetime=_past(120),
        end_datetime=_past(60),
    )
    session.add(event)
    await session.flush()

    with (
        patch("agent.quiz_workflow.query_materials", new=AsyncMock(return_value="some material")),
        patch(
            "agent.quiz_workflow.cognee_generate_quiz",
            new=AsyncMock(return_value=fake_quiz_items),
        ),
    ):
        notification_ids = await _generate_quizzes_impl(user.id, session)

    assert len(notification_ids) == 1

    notif = await session.get(Notification, notification_ids[0])
    assert notif is not None
    assert notif.status == "pending"
    assert notif.user_id == user.id
    assert notif.quiz_id is not None

    quiz = await session.get(Quiz, notif.quiz_id)
    assert quiz is not None
    assert quiz.topic == "Machine Learning"
    assert len(quiz.questions) == 2


async def test_generate_quizzes_no_events_returns_empty(session: AsyncSession):
    user = await _make_user(session, email="nobody@example.com")
    notification_ids = await _generate_quizzes_impl(user.id, session)
    assert notification_ids == []


async def test_generate_quizzes_skips_event_on_no_cognee_data(session: AsyncSession):
    """If cognee raises NoDataError the event is skipped, not the whole run."""
    from app.cognee_service import NoDataError

    user = await _make_user(session, email="skip@example.com")
    event = ScheduleEvent(
        user_id=user.id,
        type="tutorium",
        name="Algorithms",
        start_datetime=_past(60),
        end_datetime=_past(30),
    )
    session.add(event)
    await session.flush()

    with (
        patch("agent.quiz_workflow.query_materials", new=AsyncMock(return_value="")),
        patch(
            "agent.quiz_workflow.cognee_generate_quiz",
            new=AsyncMock(side_effect=NoDataError("no data")),
        ),
    ):
        notification_ids = await _generate_quizzes_impl(user.id, session)

    assert notification_ids == []


# ---------------------------------------------------------------------------
# Notification dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def connected_ws_manager():
    """Patch the ws_manager used by quiz_workflow so all users appear connected
    and sends always succeed.  Returned object exposes ``send`` for inspection."""
    send_mock = AsyncMock(return_value=True)
    with (
        patch("agent.quiz_workflow.ws_manager.is_connected", return_value=True),
        patch("agent.quiz_workflow.ws_manager.send", send_mock),
    ):
        yield send_mock


async def test_dispatch_marks_due_notifications_complete(
    session: AsyncSession, connected_ws_manager
):
    user = await _make_user(session)

    notif = Notification(
        user_id=user.id,
        status="pending",
        target_datetime=_past(1),  # already due
        content="Quiz ready!",
    )
    session.add(notif)
    await session.flush()

    dispatched = await _dispatch_due_notifications_impl(session)

    assert dispatched == 1
    await session.refresh(notif)
    assert notif.status == "complete"


async def test_dispatch_ignores_future_notifications(session: AsyncSession, connected_ws_manager):
    user = await _make_user(session)

    notif = Notification(
        user_id=user.id,
        status="pending",
        target_datetime=_future(60),  # not yet due
        content="Later quiz!",
    )
    session.add(notif)
    await session.flush()

    dispatched = await _dispatch_due_notifications_impl(session)

    assert dispatched == 0
    await session.refresh(notif)
    assert notif.status == "pending"


async def test_dispatch_ignores_already_complete_notifications(
    session: AsyncSession, connected_ws_manager
):
    user = await _make_user(session)

    notif = Notification(
        user_id=user.id,
        status="complete",
        target_datetime=_past(10),
        content="Already done.",
    )
    session.add(notif)
    await session.flush()

    dispatched = await _dispatch_due_notifications_impl(session)
    assert dispatched == 0


async def test_dispatch_defers_when_user_offline(session: AsyncSession):
    """Notifications for offline users stay pending."""
    user = await _make_user(session)

    notif = Notification(
        user_id=user.id,
        status="pending",
        target_datetime=_past(1),
        content="You're offline!",
    )
    session.add(notif)
    await session.flush()

    # ws_manager not patched → is_connected returns False
    dispatched = await _dispatch_due_notifications_impl(session)

    assert dispatched == 0
    await session.refresh(notif)
    assert notif.status == "pending"


async def test_dispatch_includes_quiz_payload(session: AsyncSession, connected_ws_manager):
    """When a notification has a quiz, its data is included in the WS payload."""
    user = await _make_user(session)
    quiz = Quiz(
        user_id=user.id,
        title="Final Quiz",
        topic="DB Systems",
        estimated_duration_minutes=10,
        questions=[{"question": "What is an index?", "answer": "A B-tree structure."}],
    )
    session.add(quiz)
    await session.flush()

    notif = Notification(
        user_id=user.id,
        status="pending",
        target_datetime=_past(1),
        content="Take the quiz!",
        quiz_id=quiz.id,
    )
    session.add(notif)
    await session.flush()

    dispatched = await _dispatch_due_notifications_impl(session)

    assert dispatched == 1
    await session.refresh(notif)
    assert notif.status == "complete"

    # Confirm the quiz payload was included in what was "sent"
    call_kwargs = connected_ws_manager.call_args
    payload = call_kwargs.args[1]  # send(user_id, payload)
    assert payload["quiz"] is not None
    assert payload["quiz"]["topic"] == "DB Systems"
