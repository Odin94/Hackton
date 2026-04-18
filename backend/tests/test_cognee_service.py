"""Unit tests for backend/app/cognee_service.py.

All cognee + litellm calls are mocked at the module boundary (see conftest.py).
No network, no filesystem writes beyond cognee's import-time logging.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app import cognee_service
from app.cognee_service import CogneeServiceError
from app.types import DiaryEntry, Material

from tests.conftest import llm_response


# ----- _sanitize -----------------------------------------------------------

def test_sanitize_strips_null_bytes():
    assert cognee_service._sanitize("hello\x00world") == "helloworld"


def test_sanitize_strips_surrounding_whitespace():
    assert cognee_service._sanitize("  hi  \n") == "hi"


def test_sanitize_rejects_empty():
    with pytest.raises(ValueError):
        cognee_service._sanitize("")


def test_sanitize_rejects_whitespace_only():
    with pytest.raises(ValueError):
        cognee_service._sanitize("   \t\n")


def test_sanitize_rejects_null_bytes_only():
    with pytest.raises(ValueError):
        cognee_service._sanitize("\x00\x00")


# ----- _wrap retryability classifier --------------------------------------

def test_wrap_timeout_instance_is_retryable():
    wrapped = cognee_service._wrap(asyncio.TimeoutError())
    assert wrapped.retryable is True


def test_wrap_builtin_timeout_is_retryable():
    wrapped = cognee_service._wrap(TimeoutError("boom"))
    assert wrapped.retryable is True


def test_wrap_connection_error_is_retryable():
    wrapped = cognee_service._wrap(ConnectionError())
    assert wrapped.retryable is True


def test_wrap_value_error_is_not_retryable():
    wrapped = cognee_service._wrap(ValueError("bad schema"))
    assert wrapped.retryable is False


def test_wrap_rate_limit_message_is_retryable():
    wrapped = cognee_service._wrap(RuntimeError("upstream returned 429 rate limit"))
    assert wrapped.retryable is True


def test_wrap_503_message_is_retryable():
    wrapped = cognee_service._wrap(RuntimeError("HTTP 503 from provider"))
    assert wrapped.retryable is True


def test_wrap_generic_error_is_not_retryable():
    wrapped = cognee_service._wrap(RuntimeError("schema validation failed"))
    assert wrapped.retryable is False


# ----- add_diary_entry / add_material --------------------------------------

@pytest.mark.asyncio
async def test_add_diary_entry_sends_formatted_body(mock_cognee):
    ts = datetime(2026, 4, 14, 9, 0, tzinfo=timezone.utc)
    entry = DiaryEntry(text="Did ML-L3 today", ts=ts, tags=["ml", "pomodoro"])

    await cognee_service.add_diary_entry(entry)

    mock_cognee.add.assert_awaited_once()
    kwargs = mock_cognee.add.await_args.kwargs
    assert kwargs["dataset_name"] == "diary"
    assert "2026-04-14T09:00:00+00:00" in kwargs["data"]
    assert "Did ML-L3 today" in kwargs["data"]
    assert "Topics: ml, pomodoro." in kwargs["data"]


@pytest.mark.asyncio
async def test_add_diary_entry_without_tags_skips_topics_suffix(mock_cognee):
    entry = DiaryEntry(text="Quick note")
    await cognee_service.add_diary_entry(entry)
    data = mock_cognee.add.await_args.kwargs["data"]
    assert "Topics:" not in data


@pytest.mark.asyncio
async def test_add_diary_entry_strips_null_bytes(mock_cognee):
    entry = DiaryEntry(text="before\x00after")
    await cognee_service.add_diary_entry(entry)
    data = mock_cognee.add.await_args.kwargs["data"]
    assert "\x00" not in data
    assert "beforeafter" in data


@pytest.mark.asyncio
async def test_add_diary_entry_rejects_empty_text(mock_cognee):
    with pytest.raises(ValueError):
        await cognee_service.add_diary_entry(DiaryEntry(text="   "))
    mock_cognee.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_diary_entry_wraps_cognee_failures(mock_cognee):
    mock_cognee.add.side_effect = RuntimeError("kuzu locked")
    with pytest.raises(CogneeServiceError) as excinfo:
        await cognee_service.add_diary_entry(DiaryEntry(text="hello"))
    assert "kuzu locked" in str(excinfo.value)
    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_add_material_sends_header_line(mock_cognee):
    mat = Material(text="Transformers explained...", source="ml-l3.md", course="ML-L3")
    await cognee_service.add_material(mat)
    kwargs = mock_cognee.add.await_args.kwargs
    assert kwargs["dataset_name"] == "materials"
    assert kwargs["data"].startswith("[source=ml-l3.md course=ML-L3]\n")
    assert "Transformers explained..." in kwargs["data"]


# ----- cognify_dataset -----------------------------------------------------

@pytest.mark.asyncio
async def test_cognify_dataset_toggles_state(mock_cognee):
    states_during_call = []

    async def slow_cognify(*args, **kwargs):
        states_during_call.append(cognee_service._state["diary"])

    mock_cognee.cognify.side_effect = slow_cognify

    await cognee_service.cognify_dataset("diary")

    assert states_during_call == ["indexing"]
    assert cognee_service._state["diary"] == "idle"


@pytest.mark.asyncio
async def test_cognify_dataset_idle_on_exception(mock_cognee):
    mock_cognee.cognify.side_effect = RuntimeError("backend exploded")
    with pytest.raises(CogneeServiceError):
        await cognee_service.cognify_dataset("diary")
    assert cognee_service._state["diary"] == "idle"


@pytest.mark.asyncio
async def test_cognify_dataset_serializes_concurrent_calls(mock_cognee):
    """Two overlapping cognify calls on the same dataset must run serially via the lock."""
    order: list[str] = []

    async def trace(*args, **kwargs):
        order.append("enter")
        await asyncio.sleep(0.01)
        order.append("exit")

    mock_cognee.cognify.side_effect = trace

    await asyncio.gather(
        cognee_service.cognify_dataset("diary"),
        cognee_service.cognify_dataset("diary"),
    )

    # No interleaving: each enter is immediately followed by its matching exit.
    assert order == ["enter", "exit", "enter", "exit"]
    assert mock_cognee.cognify.await_count == 2


@pytest.mark.asyncio
async def test_cognify_different_datasets_can_run_concurrently(mock_cognee):
    """Diary and materials have separate locks — should not serialize across datasets."""
    order: list[str] = []

    async def trace(*args, **kwargs):
        ds = kwargs["datasets"][0]
        order.append(f"enter:{ds}")
        await asyncio.sleep(0.01)
        order.append(f"exit:{ds}")

    mock_cognee.cognify.side_effect = trace

    await asyncio.gather(
        cognee_service.cognify_dataset("diary"),
        cognee_service.cognify_dataset("materials"),
    )

    # Interleaving is allowed across datasets.
    assert len(order) == 4
    # Both entered before either exited (true concurrency).
    first_exit = next(i for i, v in enumerate(order) if v.startswith("exit"))
    assert first_exit >= 2


# ----- _query / query_diary / query_materials ------------------------------

@pytest.mark.asyncio
async def test_query_diary_joins_string_results(mock_cognee):
    mock_cognee.search.return_value = ["answer one", "answer two"]
    out = await cognee_service.query_diary("what happened Monday?")
    assert out == "answer one\nanswer two"
    kwargs = mock_cognee.search.await_args.kwargs
    assert kwargs["datasets"] == ["diary"]


@pytest.mark.asyncio
async def test_query_materials_unwraps_single_string(mock_cognee):
    """cognee v1 sometimes returns a bare string for single-hit results."""
    mock_cognee.search.return_value = "the single answer"
    out = await cognee_service.query_materials("explain transformers")
    assert out == "the single answer"


@pytest.mark.asyncio
async def test_query_unwraps_single_dict(mock_cognee):
    mock_cognee.search.return_value = {"answer": "only one"}
    out = await cognee_service.query_diary("question")
    # The service stringifies each item; dict gets repr'd.
    assert "only one" in out


@pytest.mark.asyncio
async def test_query_rejects_empty(mock_cognee):
    with pytest.raises(ValueError):
        await cognee_service.query_diary("   ")


# ----- index_status --------------------------------------------------------

def test_index_status_returns_snapshot():
    snap = cognee_service.index_status()
    snap["diary"] = "MUTATED"
    assert cognee_service._state["diary"] == "idle", "index_status must return a copy"


# ----- reset ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_prunes_everything(mock_cognee):
    await cognee_service.reset()
    mock_cognee.prune_data.assert_awaited_once()
    mock_cognee.prune_system.assert_awaited_once()
    assert mock_cognee.prune_system.await_args.kwargs.get("metadata") is True


# ----- generate_quiz -------------------------------------------------------

def _chunks_with_source(name: str = "ml-l3.md", n: int = 3) -> list[dict]:
    return [
        {"text": f"chunk {i} about transformers", "is_part_of": {"name": name}}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_generate_quiz_happy_path(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = _chunks_with_source()
    items = await cognee_service.generate_quiz("transformers", n=2)

    assert len(items) == 2
    assert all(item.topic == "transformers" for item in items)
    assert all(item.source_ref == "ml-l3.md" for item in items)
    # CHUNKS search was called on materials dataset
    assert mock_cognee.search.await_args.kwargs["datasets"] == ["materials"]


@pytest.mark.asyncio
async def test_generate_quiz_empty_chunks_raises(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = []
    with pytest.raises(CogneeServiceError, match="no material"):
        await cognee_service.generate_quiz("obscure-topic", n=3)
    mock_litellm.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_quiz_normalizes_single_dict_chunk(mock_cognee, mock_litellm):
    """cognee v1 sometimes returns a single dict instead of [dict] — spec §9."""
    mock_cognee.search.return_value = {
        "text": "the only chunk", "is_part_of": {"name": "solo.md"}
    }
    items = await cognee_service.generate_quiz("solo", n=2)
    assert all(item.source_ref == "solo.md" for item in items)


@pytest.mark.asyncio
async def test_generate_quiz_source_ref_none_when_missing(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = [{"text": "no lineage here"}]
    items = await cognee_service.generate_quiz("x", n=1)
    assert items[0].source_ref is None


@pytest.mark.asyncio
async def test_generate_quiz_truncates_over_n(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = _chunks_with_source()
    # LLM returns 4 items, request asked for 2 — truncate.
    mock_litellm.return_value = llm_response(json.dumps({
        "items": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(4)]
    }))
    items = await cognee_service.generate_quiz("topic", n=2)
    assert len(items) == 2
    assert items[0].question == "Q0" and items[1].question == "Q1"


@pytest.mark.asyncio
async def test_generate_quiz_accepts_under_n_with_warning(mock_cognee, mock_litellm, caplog):
    mock_cognee.search.return_value = _chunks_with_source()
    mock_litellm.return_value = llm_response(json.dumps({
        "items": [{"question": "Q0", "answer": "A0"}]
    }))
    with caplog.at_level("WARNING"):
        items = await cognee_service.generate_quiz("topic", n=5)
    assert len(items) == 1
    assert any("expected 5" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_generate_quiz_retries_once_on_bad_json(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = _chunks_with_source()
    mock_litellm.side_effect = [
        llm_response("not json at all"),
        llm_response(json.dumps({"items": [{"question": "Q", "answer": "A"}]})),
    ]
    items = await cognee_service.generate_quiz("topic", n=1)
    assert len(items) == 1
    assert mock_litellm.await_count == 2


@pytest.mark.asyncio
async def test_generate_quiz_gives_up_after_second_bad_json(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = _chunks_with_source()
    mock_litellm.side_effect = [
        llm_response("bad 1"),
        llm_response("bad 2"),
    ]
    with pytest.raises(CogneeServiceError) as excinfo:
        await cognee_service.generate_quiz("topic", n=1)
    assert excinfo.value.retryable is True
    assert mock_litellm.await_count == 2


@pytest.mark.asyncio
async def test_generate_quiz_retries_on_malformed_items_shape(mock_cognee, mock_litellm):
    """items must be list[dict] with question+answer; anything else → retry."""
    mock_cognee.search.return_value = _chunks_with_source()
    mock_litellm.side_effect = [
        llm_response(json.dumps({"items": [{"question": "Q"}]})),  # missing answer
        llm_response(json.dumps({"items": [{"question": "Q", "answer": "A"}]})),
    ]
    items = await cognee_service.generate_quiz("topic", n=1)
    assert len(items) == 1
    assert mock_litellm.await_count == 2


@pytest.mark.asyncio
async def test_generate_quiz_rejects_empty_topic(mock_cognee, mock_litellm):
    with pytest.raises(ValueError):
        await cognee_service.generate_quiz("   ", n=3)
    mock_cognee.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_quiz_rejects_zero_n(mock_cognee, mock_litellm):
    with pytest.raises(ValueError):
        await cognee_service.generate_quiz("topic", n=0)
