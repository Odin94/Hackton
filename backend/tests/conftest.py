"""Shared fixtures for cognee-layer tests.

All tests mock cognee and litellm at the module boundary. We never hit a real
LLM or database — the cognee_service module imports cognee/litellm at the top,
so monkeypatching the attributes on the already-imported module works.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import cognee_service


@pytest.fixture(autouse=True)
def reset_state() -> None:
    """cognee_service keeps module-level dicts for concurrency; reset before each test."""
    cognee_service._state["diary"] = "idle"
    cognee_service._state["materials"] = "idle"
    cognee_service._locks["diary"] = asyncio.Lock()
    cognee_service._locks["materials"] = asyncio.Lock()


@pytest.fixture
def mock_cognee(monkeypatch: pytest.MonkeyPatch):
    """Replace cognee.add / cognify / search / prune.* with AsyncMocks."""
    add = AsyncMock(return_value=None)
    cognify = AsyncMock(return_value=None)
    search = AsyncMock(return_value=[])
    prune_data = AsyncMock(return_value=None)
    prune_system = AsyncMock(return_value=None)

    monkeypatch.setattr(cognee_service.cognee, "add", add)
    monkeypatch.setattr(cognee_service.cognee, "cognify", cognify)
    monkeypatch.setattr(cognee_service.cognee, "search", search)
    monkeypatch.setattr(
        cognee_service.cognee,
        "prune",
        SimpleNamespace(prune_data=prune_data, prune_system=prune_system),
    )
    return SimpleNamespace(
        add=add, cognify=cognify, search=search,
        prune_data=prune_data, prune_system=prune_system,
    )


@pytest.fixture
def mock_litellm(monkeypatch: pytest.MonkeyPatch):
    """Replace litellm.acompletion with a controllable AsyncMock.

    Default return: an object exposing .choices[0].message.content as a JSON string
    with two items. Tests override the return_value (or side_effect) as needed.
    """
    default_content = '{"items":[{"question":"Q1","answer":"A1"},{"question":"Q2","answer":"A2"}]}'
    acompletion = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=default_content))]
        )
    )
    monkeypatch.setattr(cognee_service.litellm, "acompletion", acompletion)
    return acompletion


def llm_response(content: str) -> SimpleNamespace:
    """Build the litellm response object tests hand back via return_value/side_effect."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
