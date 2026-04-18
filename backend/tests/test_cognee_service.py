"""Unit tests for backend/app/cognee_service.py.

All cognee + litellm calls are mocked at the module boundary (see conftest.py).
No network, no filesystem writes beyond cognee's import-time logging.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app import cognee_service
from app.cognee_service import (
    CogneeServiceError,
    LLMTimeoutError,
    MalformedLLMResponseError,
    NoDataError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.types import DiaryEntry, Material
from tests.conftest import llm_no_tool_response, llm_response

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


# ----- _wrap classifier → typed subclasses --------------------------------

def test_wrap_timeout_instance_is_upstream_error():
    wrapped = cognee_service._wrap(TimeoutError())
    assert isinstance(wrapped, UpstreamError)
    assert wrapped.retryable is True


def test_wrap_connection_error_is_upstream_error():
    wrapped = cognee_service._wrap(ConnectionError())
    assert isinstance(wrapped, UpstreamError)
    assert wrapped.retryable is True


def test_wrap_no_data_message_is_no_data_error():
    # Cognee raises NoDataError with a message like "No data found in the system, please add data first."
    wrapped = cognee_service._wrap(RuntimeError("NoDataError: No data found in the system"))
    assert isinstance(wrapped, NoDataError)
    assert wrapped.retryable is True


def test_wrap_dataset_not_found_is_no_data_error():
    """Querying a dataset that doesn't exist (e.g. empty diary) returns
    DatasetNotFoundError — map to NoDataError so callers can handle uniformly."""
    wrapped = cognee_service._wrap(RuntimeError("DatasetNotFoundError: No datasets found. (Status code: 404)"))
    assert isinstance(wrapped, NoDataError)
    assert wrapped.retryable is True


def test_wrap_value_error_is_base_class_and_not_retryable():
    wrapped = cognee_service._wrap(ValueError("bad schema"))
    # Base class, specific subclasses are NOT matched
    assert type(wrapped) is CogneeServiceError
    assert wrapped.retryable is False


def test_wrap_rate_limit_message_is_rate_limit_error():
    wrapped = cognee_service._wrap(RuntimeError("upstream returned 429 rate limit"))
    assert isinstance(wrapped, UpstreamRateLimitError)
    assert wrapped.retryable is True


def test_wrap_503_message_is_upstream_error():
    wrapped = cognee_service._wrap(RuntimeError("HTTP 503 from provider"))
    assert isinstance(wrapped, UpstreamError)
    assert wrapped.retryable is True


def test_wrap_generic_error_is_base_class_not_retryable():
    wrapped = cognee_service._wrap(RuntimeError("schema validation failed"))
    assert type(wrapped) is CogneeServiceError
    assert wrapped.retryable is False


def test_all_subclasses_are_cognee_service_error():
    """Callers catching CogneeServiceError must still catch all subclasses."""
    for cls in (NoDataError, LLMTimeoutError, MalformedLLMResponseError,
                UpstreamError, UpstreamRateLimitError):
        assert issubclass(cls, CogneeServiceError)


# ----- add_diary_entry / add_material --------------------------------------

@pytest.mark.asyncio
async def test_add_diary_entry_sends_formatted_body(mock_cognee):
    ts = datetime(2026, 4, 14, 9, 0, tzinfo=UTC)
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
async def test_add_material_ingests_as_file_preserving_source_name(mock_cognee):
    """Critical: cognee must receive an absolute file path whose filename is
    `material.source`, otherwise Document.name falls back to 'text_<md5>.txt'
    and every QuizItem.source_ref is useless gibberish (spec §10 item 4)."""
    from pathlib import Path

    captured: dict = {}

    async def capture(**kwargs):
        # Record state while the file still exists (it's deleted after cognee.add returns).
        captured.update(kwargs)
        captured["filename"] = Path(kwargs["data"]).name
        captured["content"] = Path(kwargs["data"]).read_text(encoding="utf-8")

    mock_cognee.add.side_effect = capture

    mat = Material(text="Transformers explained...", source="ml-l3.md", course="ML-L3")
    await cognee_service.add_material(mat)

    assert captured["dataset_name"] == "materials"
    # cognee must see a path, not the raw body string.
    assert Path(captured["data"]).is_absolute()
    # Filename IS the original source — the whole point of the fix.
    assert captured["filename"] == "ml-l3.md"
    # Course is attached via node_set metadata so GRAPH_COMPLETION can filter on it.
    assert captured["node_set"] == ["ML-L3"]
    # Header + text still present in the file content so the entity extractor sees them.
    assert captured["content"].startswith("[source=ml-l3.md course=ML-L3]\n")
    assert "Transformers explained..." in captured["content"]
    # Tempdir is cleaned up after cognee.add returns.
    assert not Path(captured["data"]).exists()


@pytest.mark.asyncio
async def test_add_material_sanitizes_unsafe_source_filename(mock_cognee):
    """Path-traversal attempts in `source` must not escape the tempdir."""
    from pathlib import Path

    captured: dict = {}

    async def capture(**kwargs):
        captured["filename"] = Path(kwargs["data"]).name
        captured["parent"] = str(Path(kwargs["data"]).parent)

    mock_cognee.add.side_effect = capture

    mat = Material(text="content", source="../../etc/passwd", course="C")
    await cognee_service.add_material(mat)

    assert "/" not in captured["filename"]
    assert ".." not in captured["filename"]
    # The parent is the per-call tempdir, not somewhere outside.
    assert "cognee_material_" in captured["parent"]


@pytest.mark.asyncio
async def test_add_material_cleans_up_tempdir_on_cognee_failure(mock_cognee):
    """If cognee.add raises, the tempdir is still removed — no leak."""
    from pathlib import Path

    captured: dict = {}

    async def capture(**kwargs):
        captured["data"] = kwargs["data"]
        raise RuntimeError("cognee down")

    mock_cognee.add.side_effect = capture

    mat = Material(text="c", source="x.md", course="C")
    with pytest.raises(CogneeServiceError):
        await cognee_service.add_material(mat)

    assert not Path(captured["data"]).parent.exists()


# ----- add_material_from_file ---------------------------------------------

@pytest.mark.asyncio
async def test_add_material_from_file_passes_path_directly(mock_cognee, tmp_path):
    """File-path ingest routes the real path (and course via node_set) to cognee.
    No temp write, no body mutation — cognee's loader picks text/pypdf."""
    pdf = tmp_path / "Script_01.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")  # content doesn't matter; cognee.add is mocked

    await cognee_service.add_material_from_file(pdf, course="Analysis_für_Informatik")

    kwargs = mock_cognee.add.await_args.kwargs
    assert kwargs["dataset_name"] == "materials"
    assert kwargs["data"] == str(pdf.resolve())
    assert kwargs["node_set"] == ["Analysis_für_Informatik"]


@pytest.mark.asyncio
async def test_add_material_from_file_rejects_missing_file(mock_cognee, tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    with pytest.raises(ValueError, match="not a file"):
        await cognee_service.add_material_from_file(missing, course="C")
    mock_cognee.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_material_from_file_rejects_empty_course(mock_cognee, tmp_path):
    f = tmp_path / "x.md"
    f.write_text("hi")
    with pytest.raises(ValueError, match="empty course"):
        await cognee_service.add_material_from_file(f, course="   ")


@pytest.mark.asyncio
async def test_add_material_from_file_wraps_cognee_failures(mock_cognee, tmp_path):
    mock_cognee.add.side_effect = RuntimeError("pypdf crashed on encrypted PDF")
    f = tmp_path / "x.pdf"
    f.write_bytes(b"stub")
    with pytest.raises(CogneeServiceError) as excinfo:
        await cognee_service.add_material_from_file(f, course="C")
    assert "pypdf" in str(excinfo.value)


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


@pytest.mark.asyncio
async def test_query_diary_passes_diary_system_prompt(mock_cognee):
    """Dataset isolation with access control off: the system prompt tells the
    LLM to ignore cross-dataset context. This test pins that wiring."""
    mock_cognee.search.return_value = ["ok"]
    await cognee_service.query_diary("what patterns?")
    prompt = mock_cognee.search.await_args.kwargs["system_prompt"]
    assert "diary" in prompt.lower()
    assert "ignore" in prompt.lower()
    # Explicit about excluding the other dataset.
    assert "lecture" in prompt.lower() or "material" in prompt.lower()


@pytest.mark.asyncio
async def test_query_materials_passes_materials_system_prompt(mock_cognee):
    mock_cognee.search.return_value = ["ok"]
    await cognee_service.query_materials("explain transformers")
    prompt = mock_cognee.search.await_args.kwargs["system_prompt"]
    assert "material" in prompt.lower() or "lecture" in prompt.lower()
    assert "ignore" in prompt.lower()
    assert "diary" in prompt.lower() or "journal" in prompt.lower()


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
async def test_generate_quiz_empty_chunks_raises_no_data(mock_cognee, mock_litellm):
    mock_cognee.search.return_value = []
    with pytest.raises(NoDataError, match="no material"):
        await cognee_service.generate_quiz("obscure-topic", n=3)
    mock_litellm.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_quiz_all_textless_chunks_raises_no_data(mock_cognee, mock_litellm):
    """Non-empty chunks list where every chunk has blank text → don't call LLM."""
    mock_cognee.search.return_value = [{"text": ""}, {"text": "   "}, {}]
    with pytest.raises(NoDataError, match="no text content") as excinfo:
        await cognee_service.generate_quiz("topic", n=2)
    assert excinfo.value.retryable is True
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


class _FakeDoc:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeChunk:
    def __init__(self, text: str, doc_name: str) -> None:
        self.text = text
        self.is_part_of = _FakeDoc(doc_name)


@pytest.mark.asyncio
async def test_generate_quiz_handles_pydantic_like_chunks(mock_cognee, mock_litellm):
    """Vector engines may return typed objects instead of dicts — extract by attribute."""
    mock_cognee.search.return_value = [_FakeChunk("object-shaped chunk", "typed.md")]
    items = await cognee_service.generate_quiz("topic", n=1)
    assert items[0].source_ref == "typed.md"


@pytest.mark.asyncio
async def test_generate_quiz_handles_string_is_part_of(mock_cognee, mock_litellm):
    """Some cognee configs store is_part_of as a plain name string."""
    mock_cognee.search.return_value = [
        {"text": "text", "is_part_of": "flat-name.md"}
    ]
    items = await cognee_service.generate_quiz("topic", n=1)
    assert items[0].source_ref == "flat-name.md"


# ----- _chunk_text / _extract_source_ref direct unit tests ----------------

def test_chunk_text_from_dict():
    assert cognee_service._chunk_text({"text": "hello"}) == "hello"


def test_chunk_text_from_object():
    class C:
        text = "from attr"
    assert cognee_service._chunk_text(C()) == "from attr"


def test_chunk_text_missing_returns_empty():
    assert cognee_service._chunk_text({"other": "x"}) == ""
    assert cognee_service._chunk_text(object()) == ""


def test_extract_source_ref_dict_form():
    chunk = {"is_part_of": {"name": "doc.md"}}
    assert cognee_service._extract_source_ref(chunk) == "doc.md"


def test_extract_source_ref_string_form():
    chunk = {"is_part_of": "doc.md"}
    assert cognee_service._extract_source_ref(chunk) == "doc.md"


def test_extract_source_ref_object_form():
    chunk = _FakeChunk("t", "doc.md")
    assert cognee_service._extract_source_ref(chunk) == "doc.md"


def test_extract_source_ref_missing_returns_none():
    assert cognee_service._extract_source_ref({}) is None
    assert cognee_service._extract_source_ref({"is_part_of": ""}) is None
    assert cognee_service._extract_source_ref({"is_part_of": None}) is None


def test_extract_source_ref_uses_belongs_to_set_first():
    """Cognee v1's IndexSchema payload exposes `belongs_to_set` (from
    `node_set=[course]` at ingest time) but not `is_part_of` — the course
    label is what's actually retrievable in our config."""
    chunk = {"belongs_to_set": ["ML-L3"], "is_part_of": None}
    assert cognee_service._extract_source_ref(chunk) == "ML-L3"


def test_extract_source_ref_empty_belongs_to_set_falls_through():
    chunk = {"belongs_to_set": [], "is_part_of": {"name": "fallback.md"}}
    assert cognee_service._extract_source_ref(chunk) == "fallback.md"


@pytest.mark.asyncio
async def test_generate_quiz_surfaces_course_label_as_source_ref(
    mock_cognee, mock_litellm
):
    """End-to-end: QuizItem.source_ref carries the course label from the
    `belongs_to_set` attached at ingest."""
    mock_cognee.search.return_value = [
        {"text": "lecture content", "belongs_to_set": ["ML-L3"], "is_part_of": None}
    ]
    items = await cognee_service.generate_quiz("topic", n=1)
    assert items[0].source_ref == "ML-L3"


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
    with pytest.raises(MalformedLLMResponseError) as excinfo:
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


@pytest.mark.asyncio
async def test_generate_quiz_surfaces_missing_tool_call_as_malformed(
    mock_cognee, mock_litellm
):
    """If the LLM ignores tool_choice and returns plain content, we raise
    MalformedLLMResponseError — the retry loop handles it, and after 2 tries
    we give up (this simulates a model that can't do tool-use for our schema)."""
    mock_cognee.search.return_value = _chunks_with_source()
    mock_litellm.side_effect = [
        llm_no_tool_response("Sorry, I can't do that."),
        llm_no_tool_response(""),
    ]
    with pytest.raises(MalformedLLMResponseError, match="did not call the emit_quiz tool"):
        await cognee_service.generate_quiz("topic", n=1)
    assert mock_litellm.await_count == 2


@pytest.mark.asyncio
async def test_generate_quiz_accepts_pre_parsed_tool_arguments(
    mock_cognee, mock_litellm
):
    """Some LiteLLM backends hand back dict arguments instead of a JSON string.
    _quiz_llm_call must accept both."""
    from types import SimpleNamespace

    mock_cognee.search.return_value = _chunks_with_source()
    pre_parsed = {"items": [{"question": "Q", "answer": "A"}]}
    mock_litellm.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="emit_quiz",
                                arguments=pre_parsed,  # dict, not str
                            )
                        )
                    ],
                )
            )
        ]
    )

    items = await cognee_service.generate_quiz("topic", n=1)
    assert len(items) == 1
    assert items[0].question == "Q"


@pytest.mark.asyncio
async def test_generate_quiz_uses_function_calling_not_response_format(
    mock_cognee, mock_litellm
):
    """Sanity check: verify we pass tools= + tool_choice=, not response_format.
    This ensures we stay on the path that dodges LiteLLM's response_format bug."""
    mock_cognee.search.return_value = _chunks_with_source()
    await cognee_service.generate_quiz("topic", n=1)

    kwargs = mock_litellm.await_args.kwargs
    assert "tools" in kwargs
    assert kwargs["tools"][0]["type"] == "function"
    assert kwargs["tools"][0]["function"]["name"] == "emit_quiz"
    assert kwargs["tool_choice"]["function"]["name"] == "emit_quiz"
    # Must NOT be using the problematic response_format path.
    assert "response_format" not in kwargs
    assert "extra_body" not in kwargs


@pytest.mark.asyncio
async def test_generate_quiz_llm_timeout_surfaces_as_retryable(
    mock_cognee, mock_litellm, monkeypatch
):
    """When litellm takes longer than settings.llm_call_timeout_seconds, raise retryable error."""
    mock_cognee.search.return_value = _chunks_with_source()
    monkeypatch.setattr(cognee_service.settings, "llm_call_timeout_seconds", 0.01)

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.5)
        return llm_response(json.dumps({"items": [{"question": "Q", "answer": "A"}]}))

    mock_litellm.side_effect = slow

    with pytest.raises(LLMTimeoutError) as excinfo:
        await cognee_service.generate_quiz("topic", n=1)
    assert excinfo.value.retryable is True
    assert "timeout" in str(excinfo.value).lower()
