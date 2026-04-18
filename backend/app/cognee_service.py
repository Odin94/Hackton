"""Cognee memory layer service.

Owns the two cognee datasets (`diary`, `materials`) and the contracts in
`notes/spec-cognee.md` §3. All functions are async; errors are normalized to
`CogneeServiceError` with a `.retryable` hint. Per-dataset `asyncio.Lock`s
serialize cognify calls (§7); add/query/quiz calls run freely.

Tests mock `cognee` and `litellm` at the module boundary — see
`backend/tests/conftest.py`.
"""
import asyncio
import json
import logging
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import cognee
import litellm
from cognee.api.v1.search import SearchType

from app.config import settings  # must import before cognee to normalize env
from app.types import DiaryEntry, Material, QuizItem

log = logging.getLogger(__name__)

Dataset = Literal["diary", "materials"]

_state: dict[str, Literal["idle", "indexing"]] = {"diary": "idle", "materials": "idle"}
_locks: dict[str, asyncio.Lock] = {"diary": asyncio.Lock(), "materials": asyncio.Lock()}


class CogneeServiceError(Exception):
    """Base for all cognee-service errors.

    `retryable` is a hint to the caller: True means "try again later may help"
    (timeouts, rate limits, indexing races); False means "don't retry without
    fixing something" (validation, schema, unknown). Subclasses set sensible
    defaults; callers can still isinstance-check the specific subclass.
    """

    default_retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None):
        super().__init__(message)
        self.retryable = self.default_retryable if retryable is None else retryable


class NoDataError(CogneeServiceError):
    """No chunks/results found for the query. Often means cognify hasn't run
    yet, or the index is mid-write. Caller may retry after waiting."""

    default_retryable = True


class LLMTimeoutError(CogneeServiceError):
    """Our LiteLLM call exceeded `Settings.llm_call_timeout_seconds`."""

    default_retryable = True


class MalformedLLMResponseError(CogneeServiceError):
    """LLM returned JSON we can't parse or items not matching the schema."""

    default_retryable = True


class UpstreamRateLimitError(CogneeServiceError):
    """Provider returned 429 / explicit rate-limit signal."""

    default_retryable = True


class UpstreamError(CogneeServiceError):
    """Generic transient upstream failure (connection, 502/503, class-based
    timeout from cognee/litellm internals). Retryable by default."""

    default_retryable = True


@asynccontextmanager
async def _timed(label: str, **fields: object) -> AsyncIterator[None]:
    """Log duration of a block at INFO. Fields render as key=value pairs."""
    start = time.perf_counter()
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    log.info("%s start %s", label, detail)
    try:
        yield
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.info("%s done %s elapsed_ms=%d", label, detail, elapsed_ms)


def _sanitize(text: str) -> str:
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        raise ValueError("empty text after sanitization")
    return cleaned


_TRANSIENT_CLASSES: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)
_RATE_LIMIT_MARKERS = ("rate limit", "429")
_TRANSIENT_MARKERS = ("timeout", "timed out", "503", "502")
_NO_DATA_MARKERS = ("no data found", "nodataerror", "empty knowledge graph")


def _chunk_text(chunk: object) -> str:
    """Extract the text field from a cognee chunk payload.

    Cognee's ChunksRetriever returns vector-store payloads — normally dicts with
    a `text` key, but community vector engines occasionally hand back objects
    with `.text` instead. Accept both, silently skip the rest.
    """
    if isinstance(chunk, dict):
        return str(chunk.get("text", ""))
    return str(getattr(chunk, "text", ""))


def _extract_source_ref(chunk: object) -> str | None:
    """Pull the originating document name from a chunk, if present.

    `is_part_of` may be: a dict (typical qdrant/lance payload), a pydantic-like
    object (if the vector engine returns typed DataPoints), or a bare string
    (some configs store just the document name). Return None if none apply.
    """
    if isinstance(chunk, dict):
        part = chunk.get("is_part_of")
    else:
        part = getattr(chunk, "is_part_of", None)

    if isinstance(part, dict):
        name = part.get("name")
        return str(name) if name else None
    if isinstance(part, str):
        return part or None
    name = getattr(part, "name", None)
    return str(name) if name else None


def _wrap(exc: Exception) -> CogneeServiceError:
    """Convert an arbitrary exception into the most specific CogneeServiceError
    subclass we can identify. Callers branch on type; the base class is the
    catch-all for everything we don't recognize."""
    msg = f"{type(exc).__name__}: {exc}"
    lower = msg.lower()

    if any(m in lower for m in _NO_DATA_MARKERS):
        return NoDataError(msg)
    if any(m in lower for m in _RATE_LIMIT_MARKERS):
        return UpstreamRateLimitError(msg)
    if isinstance(exc, _TRANSIENT_CLASSES) or any(m in lower for m in _TRANSIENT_MARKERS):
        return UpstreamError(msg)
    return CogneeServiceError(msg)


async def add_diary_entry(entry: DiaryEntry) -> None:
    text = _sanitize(entry.text)
    body = f"[{entry.ts.isoformat()}] {text}"
    if entry.tags:
        body += f" Topics: {', '.join(entry.tags)}."
    log.info("add_diary_entry ts=%s tags=%s", entry.ts.isoformat(), entry.tags)
    try:
        await cognee.add(data=body, dataset_name="diary")
    except Exception as e:
        raise _wrap(e) from e


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(source: str) -> str:
    """Sanitize `material.source` into a filesystem-safe filename that still
    round-trips as the Document.name cognee will store."""
    cleaned = _SAFE_FILENAME.sub("_", source).strip("._") or "material"
    # Cap to 200 chars so tempdir+filename fits under common FS limits.
    return cleaned[:200]


async def add_material(material: Material) -> None:
    """Ingest a lecture material posted as in-memory text (HTTP POST /materials).

    Writes the body to a tempdir under `material.source` as the filename,
    then hands cognee the file path — this is the only way to make
    `Document.name` (and therefore `QuizItem.source_ref`) preserve the
    original filename. Passing a raw string makes cognee generate a
    `text_<md5>.txt` name (see `save_data_to_file`), losing provenance.

    For file-backed ingest (the seed CLI's PDF/markdown path), use
    `add_material_from_file` instead — cheaper (no double-write) and
    routes PDFs through cognee's native pypdf loader.
    """
    text = _sanitize(material.text)
    body = f"[source={material.source} course={material.course}]\n{text}"
    log.info("add_material source=%s course=%s", material.source, material.course)

    # Isolated tempdir per call so concurrent adds with the same `source`
    # can't race on the file.
    scratch = tempfile.mkdtemp(prefix="cognee_material_")
    try:
        file_path = Path(scratch) / _safe_filename(material.source)
        file_path.write_text(body, encoding="utf-8")
        try:
            await cognee.add(
                data=str(file_path),
                dataset_name="materials",
                node_set=[material.course],
            )
        except Exception as e:
            raise _wrap(e) from e
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def add_material_from_file(path: Path, course: str) -> None:
    """Ingest a lecture material from an on-disk file (.pdf, .md, .txt).

    Hands the absolute path directly to cognee.add — cognee picks the right
    loader (pypdf for .pdf, text for .md/.txt) and preserves the original
    filename as `Document.name` (so `QuizItem.source_ref` shows the real
    file). `course` is attached via cognee's `node_set` metadata; downstream
    GRAPH_COMPLETION searches can filter on it via `node_name=[course]`.
    """
    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    course = course.strip()
    if not course:
        raise ValueError("empty course")
    log.info("add_material_from_file path=%s course=%s", path.name, course)
    try:
        await cognee.add(
            data=str(path.resolve()),
            dataset_name="materials",
            node_set=[course],
        )
    except Exception as e:
        raise _wrap(e) from e


async def cognify_dataset(dataset: Dataset) -> None:
    async with _locks[dataset]:
        _state[dataset] = "indexing"
        try:
            async with _timed("cognify", dataset=dataset):
                try:
                    await cognee.cognify(datasets=[dataset])
                except Exception as e:
                    log.exception("cognify failed dataset=%s", dataset)
                    raise _wrap(e) from e
        finally:
            _state[dataset] = "idle"


async def _query(dataset: Dataset, q: str) -> str:
    if not q.strip():
        raise ValueError("empty query")
    async with _timed("query", dataset=dataset, q_len=len(q)):
        try:
            results = await cognee.search(
                query_type=SearchType.GRAPH_COMPLETION,
                query_text=q,
                datasets=[dataset],
            )
        except Exception as e:
            raise _wrap(e) from e
    if isinstance(results, (str, dict)):
        results = [results]
    return "\n".join(str(r) for r in results)


async def query_diary(q: str) -> str:
    return await _query("diary", q)


async def query_materials(q: str) -> str:
    return await _query("materials", q)


def index_status() -> dict[str, Literal["idle", "indexing"]]:
    return dict(_state)


async def reset() -> None:
    log.warning("reset: pruning all cognee data")
    try:
        await cognee.prune.prune_data()
        await cognee.prune.prune_system(metadata=True)
    except Exception as e:
        raise _wrap(e) from e


_QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["question", "answer"],
            },
        },
    },
    "required": ["items"],
}


async def generate_quiz(topic: str, n: int = 5) -> list[QuizItem]:
    topic = topic.strip()
    if not topic:
        raise ValueError("empty topic")
    if n < 1:
        raise ValueError("n must be >= 1")

    async with _timed("quiz", topic=topic, n=n):
        try:
            chunks = await cognee.search(
                query_type=SearchType.CHUNKS,
                query_text=topic,
                datasets=["materials"],
                top_k=10,
            )
        except Exception as e:
            raise _wrap(e) from e

        if isinstance(chunks, dict):
            chunks = [chunks]
        if not chunks:
            raise NoDataError(
                f"no material found for topic: {topic}. "
                "Run /cognify on the materials dataset first."
            )

        context_text = "\n\n---\n\n".join(
            str(_chunk_text(c)) for c in chunks if _chunk_text(c)
        )
        if not context_text.strip():
            raise NoDataError(f"chunks for topic '{topic}' had no text content")
        source_ref = _extract_source_ref(chunks[0])

        system_prompt = (
            "You generate study quiz items strictly grounded in the provided context.\n"
            "Rules:\n"
            "1. Every question must be answerable from the context alone — no outside knowledge.\n"
            "2. Mix question types: definitional, mechanism, application, comparison.\n"
            "3. Avoid trivial restatement ('What does the context say about X?'). Test understanding.\n"
            "4. Answers must be 1–2 sentences, factual, self-contained.\n"
            f"5. Return exactly {n} items.\n"
            'Output JSON: {"items":[{"question":str,"answer":str}, ...]}.'
        )
        user_prompt = f"Topic: {topic}\n\nContext:\n{context_text}"

        # One retry on malformed LLM output only — spec §9. Timeouts/upstream
        # errors bubble up directly; retrying them at the same budget doesn't help.
        for attempt in (1, 2):
            try:
                raw_items = await _quiz_llm_call(system_prompt, user_prompt)
                break
            except MalformedLLMResponseError as e:
                if attempt == 2:
                    raise
                log.info("quiz retry after malformed response: %s", e)

        if len(raw_items) > n:
            raw_items = raw_items[:n]
        elif len(raw_items) < n:
            log.warning("quiz returned %d items, expected %d", len(raw_items), n)

        return [
            QuizItem(
                question=str(item["question"]),
                answer=str(item["answer"]),
                topic=topic,
                source_ref=source_ref,
            )
            for item in raw_items
        ]


async def _quiz_llm_call(system_prompt: str, user_prompt: str) -> list[dict]:
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                extra_body={
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "quiz",
                            "strict": True,
                            "schema": _QUIZ_SCHEMA,
                        },
                    }
                },
            ),
            timeout=settings.llm_call_timeout_seconds,
        )
    except TimeoutError as e:
        raise LLMTimeoutError(
            f"LLM call exceeded {settings.llm_call_timeout_seconds}s timeout"
        ) from e
    except Exception as e:
        raise _wrap(e) from e

    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
        items = parsed["items"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("quiz JSON parse failed: %s; content=%r", e, content[:200])
        raise MalformedLLMResponseError("quiz generation returned invalid JSON") from e

    if not isinstance(items, list) or not all(
        isinstance(i, dict) and "question" in i and "answer" in i for i in items
    ):
        log.warning("quiz items malformed: %r", str(items)[:200])
        raise MalformedLLMResponseError("quiz generation returned malformed items")
    return items
