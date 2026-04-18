from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.database import Base
from agent.models import ChatMessage, User
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
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Stored answer"))]
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
    assert body["assistant_message"]["content"] == "Stored answer"

    history_response = client.get("/chat/history", headers={"Authorization": f"Bearer {token}"})
    history = history_response.json()["messages"]
    assert [message["content"] for message in history] == [
        "What do I know about transformers?",
        "Stored answer",
    ]
