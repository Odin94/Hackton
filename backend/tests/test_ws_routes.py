from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.database import Base
from agent.models import ChatMessage, Notification
from app import chat_service, routes_auth, routes_ws
from app.routes_auth import router as auth_router
from app.routes_ws import router as ws_router


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    monkeypatch.setattr(routes_auth, "AsyncSessionLocal", factory)
    monkeypatch.setattr(chat_service, "AsyncSessionLocal", factory)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(ws_router)
    test_client = TestClient(app)
    test_client.factory = factory  # type: ignore[attr-defined]
    yield test_client
    asyncio.run(engine.dispose())


def test_ws_connect_delivers_due_notification_as_chat_message(client: TestClient) -> None:
    signup = client.post("/signup", json={"username": "ws_user"})
    assert signup.status_code == 200
    token = signup.json()["token"]
    user_id = signup.json()["user_id"]

    async def _seed_due_notification() -> None:
        async with client.factory() as session:  # type: ignore[attr-defined]
            notification = Notification(
                user_id=user_id,
                status="pending",
                target_datetime=datetime.now(UTC) - timedelta(minutes=5),
                content="Please add your Friday tutorial to your schedule.",
                quiz_id=None,
            )
            session.add(notification)
            await session.commit()

    asyncio.run(_seed_due_notification())

    with client.websocket_connect(f"/ws?token={token}") as websocket:
        first_payload = websocket.receive_json()
        second_payload = websocket.receive_json()

    payloads = [first_payload, second_payload]
    assert [payload["type"] for payload in payloads] == ["chat_message", "chat_message"]
    assert all(payload["message"]["author"] == "system" for payload in payloads)
    assert {
        payload["message"]["content"] for payload in payloads
    } == {
        "Welcome! Please enter your class and study schedule in chat so I can save it and personalize reminders and quizzes.",
        "Please add your Friday tutorial to your schedule.",
    }

    async def _load_state():
        async with client.factory() as session:  # type: ignore[attr-defined]
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
            chat_messages = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.user_id == user_id)
                        .order_by(ChatMessage.sequence_number.asc())
                    )
                )
                .scalars()
                .all()
            )
            return notifications, chat_messages

    notifications, chat_messages = asyncio.run(_load_state())
    assert [notification.status for notification in notifications] == ["complete", "complete"]
    assert [message.author for message in chat_messages] == ["system", "system"]
    assert {
        message.content for message in chat_messages
    } == {
        "Welcome! Please enter your class and study schedule in chat so I can save it and personalize reminders and quizzes.",
        "Please add your Friday tutorial to your schedule.",
    }
