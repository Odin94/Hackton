"""Integration tests for backend/app/routes_cognee.py via FastAPI TestClient.

We mount the router onto a throwaway app (no lifespan → no cognee startup calls)
and monkeypatch the service functions it delegates to. This exercises request
validation, response shapes, and HTTP error mapping.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cognee_service
from app.cognee_service import CogneeServiceError
from app.routes_cognee import router
from app.types import QuizItem


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def mock_service(monkeypatch: pytest.MonkeyPatch):
    """Replace every cognee_service entry-point the router calls."""
    mocks = {
        "add_diary_entry": AsyncMock(return_value=None),
        "add_material": AsyncMock(return_value=None),
        "cognify_dataset": AsyncMock(return_value=None),
        "query_diary": AsyncMock(return_value="diary answer"),
        "query_materials": AsyncMock(return_value="materials answer"),
        "generate_quiz": AsyncMock(return_value=[
            QuizItem(question="Q", answer="A", topic="t", source_ref="src.md")
        ]),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(cognee_service, name, mock)
    return mocks


# ----- /health ------------------------------------------------------------

def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ----- /diary, /materials POST happy paths --------------------------------

def test_post_diary_returns_queued(client: TestClient, mock_service: dict) -> None:
    r = client.post("/diary", json={"text": "Studied transformers today"})
    assert r.status_code == 200
    assert r.json() == {"status": "queued"}
    mock_service["add_diary_entry"].assert_awaited_once()


def test_post_material_returns_queued(client: TestClient, mock_service: dict) -> None:
    r = client.post(
        "/materials",
        json={"text": "Lecture notes content", "source": "ml-l3.md", "course": "ML-L3"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "queued"}


def test_post_diary_missing_field_422(client: TestClient) -> None:
    r = client.post("/diary", json={})
    assert r.status_code == 422


# ----- /cognify -----------------------------------------------------------

def test_post_cognify_returns_indexing(client: TestClient, mock_service: dict) -> None:
    r = client.post("/cognify", json={"dataset": "diary"})
    assert r.status_code == 200
    assert r.json() == {"status": "indexing"}


def test_post_cognify_invalid_dataset_422(client: TestClient) -> None:
    r = client.post("/cognify", json={"dataset": "not-real"})
    assert r.status_code == 422


# ----- /diary/query and /materials/query ----------------------------------

def test_query_diary_happy(client: TestClient, mock_service: dict) -> None:
    r = client.post("/diary/query", json={"q": "what patterns this week?"})
    assert r.status_code == 200
    assert r.json() == {"answer": "diary answer"}


def test_query_empty_q_422(client: TestClient, mock_service: dict) -> None:
    r = client.post("/diary/query", json={"q": ""})
    assert r.status_code == 422


def test_query_oversized_q_422(client: TestClient, mock_service: dict) -> None:
    r = client.post("/diary/query", json={"q": "x" * 2001})
    assert r.status_code == 422


def test_query_service_value_error_400(client: TestClient, mock_service: dict) -> None:
    mock_service["query_diary"].side_effect = ValueError("empty query")
    r = client.post("/diary/query", json={"q": "anything"})
    assert r.status_code == 400
    assert r.json()["detail"] == "empty query"


def test_query_non_retryable_cognee_error_500(client: TestClient, mock_service: dict) -> None:
    mock_service["query_diary"].side_effect = CogneeServiceError("schema failure", retryable=False)
    r = client.post("/diary/query", json={"q": "anything"})
    assert r.status_code == 500


def test_query_retryable_cognee_error_503(client: TestClient, mock_service: dict) -> None:
    mock_service["query_diary"].side_effect = CogneeServiceError("upstream 429", retryable=True)
    r = client.post("/diary/query", json={"q": "anything"})
    assert r.status_code == 503


def test_query_unknown_exception_500(client: TestClient, mock_service: dict) -> None:
    mock_service["query_diary"].side_effect = RuntimeError("surprise")
    r = client.post("/diary/query", json={"q": "anything"})
    assert r.status_code == 500


# ----- /quiz --------------------------------------------------------------

def test_quiz_happy(client: TestClient, mock_service: dict) -> None:
    r = client.post("/quiz", json={"topic": "transformers", "n": 3})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert body["items"][0]["question"] == "Q"
    assert body["items"][0]["source_ref"] == "src.md"


def test_quiz_default_n(client: TestClient, mock_service: dict) -> None:
    r = client.post("/quiz", json={"topic": "transformers"})
    assert r.status_code == 200
    # mock_service.generate_quiz received n=5 (default)
    call_kwargs = mock_service["generate_quiz"].await_args
    assert call_kwargs.args[1] == 5 or call_kwargs.kwargs.get("n") == 5


def test_quiz_n_zero_422(client: TestClient) -> None:
    r = client.post("/quiz", json={"topic": "t", "n": 0})
    assert r.status_code == 422


def test_quiz_n_too_high_422(client: TestClient) -> None:
    r = client.post("/quiz", json={"topic": "t", "n": 21})
    assert r.status_code == 422


def test_quiz_empty_topic_422(client: TestClient) -> None:
    r = client.post("/quiz", json={"topic": "", "n": 3})
    assert r.status_code == 422


def test_quiz_long_topic_422(client: TestClient) -> None:
    r = client.post("/quiz", json={"topic": "x" * 201, "n": 3})
    assert r.status_code == 422


# ----- /index-status ------------------------------------------------------

def test_index_status_returns_snapshot(client: TestClient) -> None:
    r = client.get("/index-status")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"diary", "materials"}
    assert body["diary"] in ("idle", "indexing")
