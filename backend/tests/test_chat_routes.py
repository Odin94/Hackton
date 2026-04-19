from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.database import Base
from agent.models import (
    ChatMessage,
    Course,
    Deadline,
    DemoConversationState,
    Notification,
    Quiz,
    QuizResult,
    ScheduleEvent,
    User,
)
from app import chat_service, routes_auth, routes_chat
from app.routes_auth import router as auth_router
from app.routes_chat import router as chat_router
from app.types import QuizItem


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    monkeypatch.setattr(routes_auth, "AsyncSessionLocal", factory)
    monkeypatch.setattr(routes_chat, "AsyncSessionLocal", factory, raising=False)
    monkeypatch.setattr(chat_service, "AsyncSessionLocal", factory)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat_router)
    test_client = TestClient(app)
    test_client.factory = factory  # type: ignore[attr-defined]
    yield test_client
    asyncio.run(engine.dispose())


def _insert_user(client: TestClient, username: str) -> User:
    async def _run() -> User:
        async with client.factory() as session:  # type: ignore[attr-defined]
            user = User(username=username)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return asyncio.run(_run())


def _insert_message(client: TestClient, *, user_id: int, author: str, seq: int, content: str) -> ChatMessage:
    async def _run() -> ChatMessage:
        async with client.factory() as session:  # type: ignore[attr-defined]
            message = ChatMessage(
                user_id=user_id,
                author=author,
                sequence_number=seq,
                content=content,
            )
            session.add(message)
            await session.commit()
            await session.refresh(message)
            return message

    return asyncio.run(_run())


def _login(client: TestClient, username: str) -> str:
    response = client.post("/login", json={"username": username})
    assert response.status_code == 200
    return response.json()["token"]


def _tool_response(
    *,
    content: str | None = None,
    tool_calls: list | None = None,
    usage: dict[str, int] | None = None,
):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        model_dump=lambda exclude_none=True: {
            k: v
            for k, v in {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }.items()
            if not (exclude_none and v is None)
        },
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


def test_chat_history_requires_token(client: TestClient) -> None:
    response = client.get("/chat/history")
    assert response.status_code == 401


def test_chat_history_returns_messages_for_user(client: TestClient) -> None:
    user = _insert_user(client, "alice")
    _insert_message(client, user_id=user.id, author="user", seq=1, content="hello")
    _insert_message(client, user_id=user.id, author="system", seq=2, content="hi there")
    token = _login(client, "alice")

    response = client.get("/chat/history", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert [message["author"] for message in body["messages"]] == ["user", "system"]
    assert body["messages"][1]["content"] == "hi there"


def test_post_chat_message_persists_turn(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert_user(client, "bob")
    token = _login(client, "bob")

    monkeypatch.setattr(chat_service, "_build_cognee_context", AsyncMock(return_value="materials"))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            return_value=_tool_response(
                content="Stored answer",
                usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            )
        ),
    )

    response = client.post(
        "/chat/messages",
        json={"content": "What do I know about transformers?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_message"]["sequence_number"] == 1
    assert body["assistant_message"]["sequence_number"] == 2
    assert "Stored answer" in body["assistant_message"]["content"]
    assert "[tokens: prompt=11, completion=7, total=18]" in body["assistant_message"]["content"]
    assert isinstance(body["assistant_message"]["processing_ms"], int)
    assert body["assistant_message"]["processing_ms"] >= 0
    assert body["user_message"]["processing_ms"] is None

    history_response = client.get("/chat/history", headers={"Authorization": f"Bearer {token}"})
    history = history_response.json()["messages"]
    assert history[0]["content"] == "What do I know about transformers?"
    assert "Stored answer" in history[1]["content"]
    assert "[tokens: prompt=11, completion=7, total=18]" in history[1]["content"]


def test_signup_creates_course_prompt_notification(client: TestClient) -> None:
    response = client.post("/signup", json={"username": "newbie"})

    assert response.status_code == 200
    user_id = response.json()["user_id"]

    async def _load() -> list[Notification]:
        async with client.factory() as session:  # type: ignore[attr-defined]
            rows = await session.execute(
                select(Notification).where(Notification.user_id == user_id)
            )
            return list(rows.scalars().all())

    notifications = asyncio.run(_load())
    assert len(notifications) == 1
    assert notifications[0].status == "pending"
    assert "what courses do you have" in notifications[0].content.lower()


def test_post_chat_message_adds_schedule_events(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post("/signup", json={"username": "schedule_user"})
    assert response.status_code == 200
    token = response.json()["token"]
    user_id = response.json()["user_id"]

    async def _seed_existing_event() -> tuple[int, int]:
        async with client.factory() as session:  # type: ignore[attr-defined]
            math = Course(user_id=user_id, name="Math")
            databases = Course(user_id=user_id, name="Databases")
            session.add_all([math, databases])
            await session.flush()
            session.add(
                ScheduleEvent(
                    user_id=user_id,
                    course_id=math.id,
                    type="lecture",
                    name="Existing math lecture",
                    start_datetime=chat_service._parse_iso_datetime("2026-04-19T08:00:00+02:00"),
                    end_datetime=chat_service._parse_iso_datetime("2026-04-19T09:00:00+02:00"),
                )
            )
            await session.commit()
            return math.id, databases.id

    math_id, databases_id = asyncio.run(_seed_existing_event())

    monkeypatch.setattr(chat_service, "_build_cognee_context", AsyncMock(return_value="materials"))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-1",
                            function=SimpleNamespace(
                                name="add_schedule_events",
                                arguments=(
                                    '{"events":['
                                    f'{{"type":"lecture","course_id":{databases_id},"name":"Databases",'
                                    '"start_datetime":"2026-04-20T09:00:00+02:00",'
                                    '"end_datetime":"2026-04-20T10:30:00+02:00"},'
                                    f'{{"type":"study session","course_id":{math_id},"name":"Algorithms review",'
                                    '"start_datetime":"2026-04-21T14:00:00+02:00",'
                                    '"end_datetime":"2026-04-21T16:00:00+02:00"}'
                                    ']}'
                                ),
                            ),
                        )
                    ]
                ),
                _tool_response(content="I saved those schedule events to your calendar."),
            ]
        ),
    )

    post_response = client.post(
        "/chat/messages",
        json={"content": "My schedule is databases Monday 9-10:30 and study session Tuesday 14-16."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert post_response.status_code == 200
    assistant_content = post_response.json()["assistant_message"]["content"]
    assert "add schedule events: added 2 event(s): Databases, Algorithms review" in assistant_content
    assert "I saved those schedule events to your calendar." in assistant_content

    async def _load_state():
        async with client.factory() as session:  # type: ignore[attr-defined]
            events = (
                (
                    await session.execute(
                        select(ScheduleEvent)
                        .where(ScheduleEvent.user_id == user_id)
                        .order_by(ScheduleEvent.start_datetime.asc())
                    )
                )
                .scalars()
                .all()
            )
            notifications = (
                (
                    await session.execute(
                        select(Notification)
                        .where(Notification.user_id == user_id)
                        .order_by(Notification.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            return events, notifications

    events, notifications = asyncio.run(_load_state())
    assert [event.name for event in events] == [
        "Existing math lecture",
        "Databases",
        "Algorithms review",
    ]
    assert [event.type for event in events] == ["lecture", "lecture", "study session"]
    assert notifications[0].status == "pending"


def test_post_chat_message_adds_courses_and_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/signup", json={"username": "onboard_user"})
    assert response.status_code == 200
    token = response.json()["token"]
    user_id = response.json()["user_id"]

    monkeypatch.setattr(chat_service, "_build_cognee_context", AsyncMock(return_value="materials"))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-1",
                            function=SimpleNamespace(
                                name="add_courses",
                                arguments='{"courses":["Algorithms","Databases"]}',
                            ),
                        )
                    ]
                ),
                _tool_response(
                    content="I added Algorithms and Databases. Now send me the schedule for both courses."
                ),
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-2",
                            function=SimpleNamespace(
                                name="add_deadlines",
                                arguments='{"deadlines":[{"course_id":1,"name":"Algorithms exam","datetime":"2026-05-10T09:00:00+02:00"}]}',
                            ),
                        )
                    ]
                ),
                _tool_response(
                    content="I added the Algorithms exam deadline. I still need deadlines for Databases."
                ),
            ]
        ),
    )

    courses_response = client.post(
        "/chat/messages",
        json={"content": "I have Algorithms and Databases this semester."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert courses_response.status_code == 200
    assert "add courses: added 2 course(s): Algorithms (id=1), Databases (id=2)" in courses_response.json()["assistant_message"]["content"]

    deadlines_response = client.post(
        "/chat/messages",
        json={"content": "Algorithms exam is on 2026-05-10 at 09:00."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deadlines_response.status_code == 200
    assert "add deadlines: added 1 deadline(s): Algorithms exam; still missing deadlines for: Databases" in deadlines_response.json()["assistant_message"]["content"]

    async def _load_state():
        async with client.factory() as session:  # type: ignore[attr-defined]
            courses = (
                (
                    await session.execute(
                        select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            deadlines = (
                (
                    await session.execute(
                        select(Deadline).where(Deadline.user_id == user_id).order_by(Deadline.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            notifications = (
                (
                    await session.execute(
                        select(Notification).where(Notification.user_id == user_id).order_by(Notification.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            return courses, deadlines, notifications

    courses, deadlines, notifications = asyncio.run(_load_state())
    assert [course.name for course in courses] == ["Algorithms", "Databases"]
    assert deadlines[0].name == "Algorithms exam"
    assert deadlines[0].course_id == courses[0].id
    assert notifications[0].status == "complete"


def test_add_deadlines_queues_profile_question_when_onboarding_reaches_final_step(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/signup", json={"username": "profile_prompt_user"})
    assert response.status_code == 200
    token = response.json()["token"]
    user_id = response.json()["user_id"]

    async def _seed_course() -> int:
        async with client.factory() as session:  # type: ignore[attr-defined]
            course = Course(user_id=user_id, name="Algorithms")
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course.id

    course_id = asyncio.run(_seed_course())

    monkeypatch.setattr(chat_service, "_build_cognee_context", AsyncMock(return_value="materials"))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-1",
                            function=SimpleNamespace(
                                name="add_deadlines",
                                arguments=(
                                    '{"deadlines":['
                                    f'{{"course_id":{course_id},"name":"Algorithms exam",'
                                    '"datetime":"2026-05-10T09:00:00+02:00"}'
                                    "]}"
                                ),
                            ),
                        )
                    ]
                ),
                _tool_response(
                    content=(
                        "I added your deadline. Last onboarding question: what are your "
                        "interests, and what are your goals for the future?"
                    )
                ),
            ]
        ),
    )

    deadlines_response = client.post(
        "/chat/messages",
        json={"content": "Algorithms exam is on 2026-05-10 at 09:00."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deadlines_response.status_code == 200

    async def _load_notifications() -> list[Notification]:
        async with client.factory() as session:  # type: ignore[attr-defined]
            rows = await session.execute(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.id.asc())
            )
            return list(rows.scalars().all())

    notifications = asyncio.run(_load_notifications())
    assert notifications[-1].status == "pending"
    assert "what are your interests, and what are your goals for the future" in notifications[-1].content.lower()


def test_post_chat_message_saves_user_interests_and_future_goals(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/signup", json={"username": "profile_save_user"})
    assert response.status_code == 200
    token = response.json()["token"]
    user_id = response.json()["user_id"]

    async def _seed_profile_prompt() -> None:
        async with client.factory() as session:  # type: ignore[attr-defined]
            session.add(
                Notification(
                    user_id=user_id,
                    status="pending",
                    target_datetime=chat_service.datetime.now(chat_service.UTC),
                    content=(
                        "Last onboarding question: what are your interests, and what are your "
                        "goals for the future? I'll save that to your profile."
                    ),
                    quiz_id=None,
                )
            )
            await session.commit()

    asyncio.run(_seed_profile_prompt())

    monkeypatch.setattr(chat_service, "_build_cognee_context", AsyncMock(return_value="materials"))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-1",
                            function=SimpleNamespace(
                                name="save_user_profile",
                                arguments=(
                                    '{"interests":"Distributed systems, AI products",'
                                    '"future_goals":"Build useful study tools and become an ML engineer"}'
                                ),
                            ),
                        )
                    ]
                ),
                _tool_response(
                    content="Thanks, I saved your interests and future goals to your profile."
                ),
            ]
        ),
    )

    profile_response = client.post(
        "/chat/messages",
        json={
            "content": (
                "I'm interested in distributed systems and AI products. In the future I want "
                "to build useful study tools and become an ML engineer."
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile_response.status_code == 200
    assert "save user profile: saved interests='Distributed systems, AI products'" in profile_response.json()["assistant_message"]["content"]

    async def _load_state() -> tuple[User | None, list[Notification]]:
        async with client.factory() as session:  # type: ignore[attr-defined]
            user = await session.get(User, user_id)
            rows = await session.execute(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.id.asc())
            )
            return user, list(rows.scalars().all())

    user, notifications = asyncio.run(_load_state())
    assert user is not None
    assert user.interests == "Distributed systems, AI products"
    assert user.future_goals == "Build useful study tools and become an ML engineer"
    assert notifications[-1].status == "complete"


def test_post_chat_message_recovers_from_invalid_schedule_tool_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/signup", json={"username": "recover_user"})
    assert response.status_code == 200
    token = response.json()["token"]
    user_id = response.json()["user_id"]

    async def _seed_course() -> int:
        async with client.factory() as session:  # type: ignore[attr-defined]
            course = Course(user_id=user_id, name="Distributed systems")
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course.id

    course_id = asyncio.run(_seed_course())

    monkeypatch.setattr(chat_service, "_build_cognee_context", AsyncMock(return_value="materials"))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-1",
                            function=SimpleNamespace(
                                name="add_schedule_events",
                                arguments=(
                                    '{"events":['
                                    f'{{"type":"lecture","course_id":{course_id},"name":"Distributed systems",'
                                    '"start_datetime":"monday at nine",'
                                    '"end_datetime":"monday at ten"}'
                                    ']}'
                                ),
                            ),
                        )
                    ]
                ),
                _tool_response(
                    content="I couldn’t save that yet. Please send each event with a specific date and start/end time."
                ),
            ]
        ),
    )

    post_response = client.post(
        "/chat/messages",
        json={"content": "I have distributed systems on Monday around nine."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert post_response.status_code == 200
    assistant_content = post_response.json()["assistant_message"]["content"]
    assert "add schedule events failed: Invalid isoformat string: 'monday at nine'" in assistant_content
    assert "Please send each event" in assistant_content

    history_response = client.get("/chat/history", headers={"Authorization": f"Bearer {token}"})
    history = history_response.json()["messages"]
    assert history[-2]["content"] == "I have distributed systems on Monday around nine."
    assert "Please send each event" in history[-1]["content"]

    async def _load_events() -> list[ScheduleEvent]:
        async with client.factory() as session:  # type: ignore[attr-defined]
            events = (
                (
                    await session.execute(
                        select(ScheduleEvent)
                        .where(ScheduleEvent.user_id == user_id)
                        .order_by(ScheduleEvent.start_datetime.asc())
                    )
                )
                .scalars()
                .all()
            )
            return list(events)

    events = asyncio.run(_load_events())
    assert events == []


def test_demo_trigger_schedules_and_delivers_chat_notification(client: TestClient) -> None:
    user = _insert_user(client, "demo_user")
    token = _login(client, "demo_user")

    response = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Algorithms"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["notification_message"]["author"] == "system"
    assert (
        body["notification_message"]["content"]
        == "Hey you finished your Algorithms lecture 20 minutes ago! Did you get through today's slides?"
    )

    history_response = client.get("/chat/history", headers={"Authorization": f"Bearer {token}"})
    history = history_response.json()["messages"]
    assert history[-1]["content"] == body["notification_message"]["content"]

    async def _load_state():
        async with client.factory() as session:  # type: ignore[attr-defined]
            notification = await session.scalar(
                select(Notification)
                .where(Notification.user_id == user.id)
                .order_by(Notification.id.desc())
                .limit(1)
            )
            demo_state = await session.scalar(
                select(DemoConversationState).where(DemoConversationState.user_id == user.id)
            )
            return notification, demo_state

    notification, demo_state = asyncio.run(_load_state())
    assert notification is not None
    assert notification.status == "complete"
    assert demo_state is not None
    assert demo_state.status == "awaiting_slides_progress"
    assert demo_state.course_name == "Algorithms"


def test_demo_chat_message_with_coverage_returns_interactive_quiz_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _insert_user(client, "demo_quiz_payload_user")
    token = _login(client, "demo_quiz_payload_user")

    quiz_items = [
        QuizItem(
            question="What does self-attention compute?",
            answer="It computes context-aware token representations.",
            options=[
                "It computes context-aware token representations.",
                "It stores positional embeddings.",
                "It normalizes batch statistics.",
                "It applies dropout.",
            ],
            correct_index=0,
            topic="transformers",
            source_ref="Machine Learning",
        ),
        QuizItem(
            question="Why do transformers use positional information?",
            answer="Because attention alone does not encode token order.",
            options=[
                "Because attention alone does not encode token order.",
                "To reduce parameter count.",
                "To enable dropout.",
                "To simplify backprop.",
            ],
            correct_index=0,
            topic="transformers",
            source_ref="Machine Learning",
        ),
    ]
    monkeypatch.setattr(chat_service.cognee_service, "generate_quiz", AsyncMock(return_value=quiz_items))
    llm_mock = AsyncMock()
    monkeypatch.setattr(chat_service.litellm, "acompletion", llm_mock)

    trigger = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Machine Learning"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert trigger.status_code == 200

    response = client.post(
        "/chat/messages",
        json={"content": "We got through about 50% of the slides."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "I generated a quick quiz covering about 50% of today's slides" in body["assistant_message"]["content"]
    assert body["demo_quiz"] is not None
    assert body["demo_quiz"]["assistant_message"]["id"] == body["assistant_message"]["id"]
    assert len(body["demo_quiz"]["questions"]) == 2
    assert "What does self-attention compute?" not in body["assistant_message"]["content"]
    assert llm_mock.await_count == 0

    async def _load_demo_state():
        async with client.factory() as session:  # type: ignore[attr-defined]
            demo_state = await session.scalar(
                select(DemoConversationState).where(DemoConversationState.user_id == user.id)
            )
            quiz = await session.scalar(select(Quiz).where(Quiz.user_id == user.id).limit(1))
            return demo_state, quiz

    demo_state, quiz = asyncio.run(_load_demo_state())
    assert demo_state is not None
    assert demo_state.status == "awaiting_quiz_answers"
    assert quiz is not None


def test_demo_energy_checkin_prompt_excludes_unrelated_history_and_deadlines(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _insert_user(client, "demo_prompt_user")
    token = _login(client, "demo_prompt_user")
    _insert_message(
        client,
        user_id=user.id,
        author="system",
        seq=1,
        content="Your next deadline is tomorrow at 9am.",
    )
    _insert_message(
        client,
        user_id=user.id,
        author="user",
        seq=2,
        content="Can you remind me about the database exam?",
    )

    quiz_items = [
        QuizItem(
            question="What is backpropagation used for?",
            answer="It computes gradients for learning.",
            options=[
                "It computes gradients for learning.",
                "It samples mini-batches.",
                "It initializes weights.",
                "It shuffles the dataset.",
            ],
            correct_index=0,
            topic="backprop",
            source_ref="Machine Learning",
        )
    ]
    monkeypatch.setattr(chat_service.cognee_service, "generate_quiz", AsyncMock(return_value=quiz_items))

    captured_messages: list[dict] = []

    async def _fake_completion(*args, **kwargs):
        nonlocal captured_messages
        captured_messages = kwargs["messages"]
        return _tool_response(
            content="Great, you should do a study session in the library.",
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )

    monkeypatch.setattr(chat_service.litellm, "acompletion", AsyncMock(side_effect=_fake_completion))

    trigger = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Machine Learning"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert trigger.status_code == 200

    start = client.post(
        "/chat/demo-quiz",
        json={"coverage_percent": 50, "question_count": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start.status_code == 200

    complete = client.post(
        "/chat/demo-quiz/complete",
        json={"correct_answers": 1, "false_answers": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert complete.status_code == 200

    async def _insert_deadline():
        async with client.factory() as session:  # type: ignore[attr-defined]
            course = Course(user_id=user.id, name="Databases")
            session.add(course)
            await session.flush()
            session.add(
                Deadline(
                    user_id=user.id,
                    course_id=course.id,
                    name="Database exam",
                    datetime=chat_service._parse_iso_datetime("2026-04-20T09:00:00+02:00"),
                )
            )
            await session.commit()

    asyncio.run(_insert_deadline())

    response = client.post(
        "/chat/messages",
        json={"content": "I'm feeling pretty good."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"].startswith(
        "Great, you should do a study session in the library."
    )

    user_prompt = next(
        message["content"]
        for message in captured_messages
        if message.get("role") == "user"
    )
    assert "Recent chat history:" in user_prompt
    assert "How energized and focused are you feeling today?" in user_prompt
    assert "Can you remind me about the database exam?" not in user_prompt
    assert "Your next deadline is tomorrow at 9am." not in user_prompt
    assert "Upcoming deadlines:" not in user_prompt
    assert "Database exam" not in user_prompt


def test_demo_flow_recommends_library_after_interactive_quiz(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _insert_user(client, "demo_flow_user")
    token = _login(client, "demo_flow_user")

    quiz_items = [
        QuizItem(
            question="What does self-attention compute?",
            answer="It computes context-aware token representations.",
            options=[
                "It computes context-aware token representations.",
                "It stores positional embeddings.",
                "It normalizes batch statistics.",
                "It applies dropout.",
            ],
            correct_index=0,
            topic="transformers",
            source_ref="Machine Learning",
        ),
        QuizItem(
            question="Why do transformers use positional information?",
            answer="Because attention alone does not encode token order.",
            options=[
                "Because attention alone does not encode token order.",
                "To reduce parameter count.",
                "To enable dropout.",
                "To simplify backprop.",
            ],
            correct_index=0,
            topic="transformers",
            source_ref="Machine Learning",
        ),
    ]
    monkeypatch.setattr(chat_service.cognee_service, "generate_quiz", AsyncMock(return_value=quiz_items))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-complete",
                            function=SimpleNamespace(
                                name="complete_demo_flow",
                                arguments='{"outcome":"library_study_session"}',
                            ),
                        )
                    ]
                ),
                _tool_response(content="Great, you should do a study session in the library."),
            ]
        ),
    )

    trigger = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Machine Learning"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert trigger.status_code == 200

    start = client.post(
        "/chat/demo-quiz",
        json={"coverage_percent": 50, "question_count": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start.status_code == 200
    assert len(start.json()["questions"]) == 2

    complete = client.post(
        "/chat/demo-quiz/complete",
        json={"correct_answers": 2, "false_answers": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert complete.status_code == 200
    feedback_text = complete.json()["assistant_message"]["content"]
    assert "You got 2/2 right, which is 100%." in feedback_text
    assert "How energized and focused are you feeling today?" in feedback_text

    recommendation_response = client.post(
        "/chat/messages",
        json={"content": "I'm feeling good and ready for action."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert recommendation_response.status_code == 200
    recommendation_text = recommendation_response.json()["assistant_message"]["content"]
    assert recommendation_text == "Great, you should do a study session in the library."
    assert "finish the demo flow" not in recommendation_text
    assert "Calling the demo completion" not in recommendation_text
    assert "What does self-attention compute?" not in recommendation_text

    async def _load_demo_data():
        async with client.factory() as session:  # type: ignore[attr-defined]
            demo_state = await session.scalar(
                select(DemoConversationState).where(DemoConversationState.user_id == user.id)
            )
            quiz = await session.scalar(select(Quiz).where(Quiz.user_id == user.id).limit(1))
            result = await session.scalar(
                select(QuizResult).where(QuizResult.user_id == user.id).limit(1)
            )
            return demo_state, quiz, result

    demo_state, quiz, result = asyncio.run(_load_demo_data())
    assert demo_state is not None
    assert demo_state.status == "complete"
    assert quiz is not None
    assert result is not None
    assert result.correct_answers == 2
    assert result.false_answers == 0


def test_demo_flow_recommends_rest_after_interactive_quiz(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert_user(client, "demo_rest_user")
    token = _login(client, "demo_rest_user")

    quiz_items = [
        QuizItem(
            question="What is backpropagation used for?",
            answer="It computes gradients for learning.",
            options=[
                "It computes gradients for learning.",
                "It samples mini-batches.",
                "It initializes weights.",
                "It shuffles the dataset.",
            ],
            correct_index=0,
            topic="backprop",
            source_ref="Machine Learning",
        )
    ]
    monkeypatch.setattr(chat_service.cognee_service, "generate_quiz", AsyncMock(return_value=quiz_items))
    monkeypatch.setattr(
        chat_service.litellm,
        "acompletion",
        AsyncMock(
            side_effect=[
                _tool_response(
                    tool_calls=[
                        SimpleNamespace(
                            id="tool-complete",
                            function=SimpleNamespace(
                                name="complete_demo_flow",
                                arguments='{"outcome":"rest_and_recover"}',
                            ),
                        )
                    ]
                ),
                _tool_response(content="You should take the afternoon off and relax to recover."),
            ]
        ),
    )

    trigger = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Machine Learning"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert trigger.status_code == 200

    start = client.post(
        "/chat/demo-quiz",
        json={"coverage_percent": 50, "question_count": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start.status_code == 200

    complete = client.post(
        "/chat/demo-quiz/complete",
        json={"correct_answers": 1, "false_answers": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert complete.status_code == 200

    third_turn = client.post(
        "/chat/messages",
        json={"content": "I'm feeling low energy and tired."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert third_turn.status_code == 200
    assert (
        third_turn.json()["assistant_message"]["content"]
        == "You should take the afternoon off and relax to recover."
    )


def test_interactive_demo_quiz_routes_store_quiz_and_advance_demo_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _insert_user(client, "interactive_demo_user")
    token = _login(client, "interactive_demo_user")

    quiz_items = [
        QuizItem(
            question="What does self-attention compute?",
            answer="It mixes token information into contextualized representations.",
            options=[
                "It mixes token information into contextualized representations.",
                "It sorts the vocabulary alphabetically.",
                "It fixes vanishing gradients by clipping them.",
                "It removes the need for embeddings.",
            ],
            correct_index=0,
            topic="transformers",
            source_ref="Machine Learning",
        ),
        QuizItem(
            question="Why is positional information needed in transformers?",
            answer="Self-attention alone does not encode token order.",
            options=[
                "It makes matrix multiplication cheaper.",
                "Self-attention alone does not encode token order.",
                "It guarantees linear memory use.",
                "It replaces the output layer.",
            ],
            correct_index=1,
            topic="transformers",
            source_ref="Machine Learning",
        ),
    ]
    monkeypatch.setattr(chat_service.cognee_service, "generate_quiz", AsyncMock(return_value=quiz_items))

    trigger = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Machine Learning"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert trigger.status_code == 200

    start = client.post(
        "/chat/demo-quiz",
        json={"coverage_percent": 50, "question_count": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert start.status_code == 200
    start_body = start.json()
    assert start_body["title"] == "Demo quiz: Machine Learning"
    assert len(start_body["questions"]) == 2
    assert "first 50% of today's slides" in start_body["assistant_message"]["content"]

    complete = client.post(
        "/chat/demo-quiz/complete",
        json={"correct_answers": 1, "false_answers": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert complete.status_code == 200
    complete_text = complete.json()["assistant_message"]["content"]
    assert "You got 1/2 right, which is 50%." in complete_text
    assert "How energized and focused are you feeling today?" in complete_text

    async def _load_demo_data():
        async with client.factory() as session:  # type: ignore[attr-defined]
            demo_state = await session.scalar(
                select(DemoConversationState).where(DemoConversationState.user_id == user.id)
            )
            quiz = await session.scalar(select(Quiz).where(Quiz.user_id == user.id).limit(1))
            result = await session.scalar(
                select(QuizResult).where(QuizResult.user_id == user.id).limit(1)
            )
            messages = (
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
            return demo_state, quiz, result, messages

    demo_state, quiz, result, messages = asyncio.run(_load_demo_data())
    assert demo_state is not None
    assert demo_state.status == "awaiting_energy_checkin"
    assert quiz is not None
    assert result is not None
    assert result.correct_answers == 1
    assert result.false_answers == 1
    assert any(
        "multiple-choice popup" in message.content
        for message in messages
        if message.author == "system"
    )


def test_demo_trigger_clears_old_demo_system_messages_from_history(client: TestClient) -> None:
    user = _insert_user(client, "demo_reset_user")
    token = _login(client, "demo_reset_user")

    _insert_message(
        client,
        user_id=user.id,
        author="system",
        seq=1,
        content=(
            "You got 0/3 right, which is 0%. That's worse than your average of 72%.\n\n"
            "How energized and focused are you feeling today?"
        ),
    )
    _insert_message(
        client,
        user_id=user.id,
        author="system",
        seq=2,
        content=(
            "Okay, here's your quiz covering the first 50% of today's slides. "
            "Use the multiple-choice popup and I'll score it when you finish."
        ),
    )
    _insert_message(
        client,
        user_id=user.id,
        author="system",
        seq=3,
        content="Non-demo system message that should stay visible.",
    )

    trigger = client.post(
        "/chat/demo-trigger",
        json={"course_name": "Machine Learning"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert trigger.status_code == 200

    history = client.get(
        "/chat/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert history.status_code == 200
    contents = [message["content"] for message in history.json()["messages"]]
    assert any("Did you get through today's slides?" in content for content in contents)
    assert any("Non-demo system message that should stay visible." == content for content in contents)
    assert not any("How energized and focused are you feeling today?" in content for content in contents)
    assert not any("multiple-choice popup" in content for content in contents)
