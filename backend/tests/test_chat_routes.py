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
from agent.models import ChatMessage, Notification, ScheduleEvent, User
from app import chat_service, routes_auth, routes_chat
from app.routes_auth import router as auth_router
from app.routes_chat import router as chat_router


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


def test_signup_creates_schedule_prompt_notification(client: TestClient) -> None:
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
    assert "enter your class and study schedule" in notifications[0].content.lower()


def test_post_chat_message_adds_schedule_events(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post("/signup", json={"username": "schedule_user"})
    assert response.status_code == 200
    token = response.json()["token"]
    user_id = response.json()["user_id"]

    async def _seed_existing_event() -> None:
        async with client.factory() as session:  # type: ignore[attr-defined]
            session.add(
                ScheduleEvent(
                    user_id=user_id,
                    type="lecture",
                    name="Existing math lecture",
                    start_datetime=chat_service._parse_iso_datetime("2026-04-19T08:00:00+02:00"),
                    end_datetime=chat_service._parse_iso_datetime("2026-04-19T09:00:00+02:00"),
                )
            )
            await session.commit()

    asyncio.run(_seed_existing_event())

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
                                    '{"type":"lecture","name":"Databases",'
                                    '"start_datetime":"2026-04-20T09:00:00+02:00",'
                                    '"end_datetime":"2026-04-20T10:30:00+02:00"},'
                                    '{"type":"study session","name":"Algorithms review",'
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
    assert "add schedule events: added 2 event(s) to the user's schedule" in assistant_content
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
    assert notifications[0].status == "complete"


def test_post_chat_message_recovers_from_invalid_schedule_tool_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/signup", json={"username": "recover_user"})
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
                                name="add_schedule_events",
                                arguments=(
                                    '{"events":['
                                    '{"type":"lecture","name":"Distributed systems",'
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
