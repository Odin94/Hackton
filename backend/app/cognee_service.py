import asyncio
import json
import logging
from typing import Literal

from app.config import settings  # must import before cognee to normalize env

import cognee
import litellm
from cognee.api.v1.search import SearchType

from app.types import DiaryEntry, Material, QuizItem

log = logging.getLogger(__name__)

Dataset = Literal["diary", "materials"]

_state: dict[str, Literal["idle", "indexing"]] = {"diary": "idle", "materials": "idle"}
_locks: dict[str, asyncio.Lock] = {"diary": asyncio.Lock(), "materials": asyncio.Lock()}


class CogneeServiceError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _sanitize(text: str) -> str:
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        raise ValueError("empty text after sanitization")
    return cleaned


_RETRYABLE_CLASSES: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
)
_RETRYABLE_MARKERS = ("timeout", "timed out", "rate limit", "429", "503", "502")


def _wrap(exc: Exception) -> CogneeServiceError:
    msg = f"{type(exc).__name__}: {exc}"
    retryable = isinstance(exc, _RETRYABLE_CLASSES) or any(
        marker in msg.lower() for marker in _RETRYABLE_MARKERS
    )
    return CogneeServiceError(msg, retryable=retryable)


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


async def add_material(material: Material) -> None:
    text = _sanitize(material.text)
    body = f"[source={material.source} course={material.course}]\n{text}"
    log.info("add_material source=%s course=%s", material.source, material.course)
    try:
        await cognee.add(data=body, dataset_name="materials")
    except Exception as e:
        raise _wrap(e) from e


async def cognify_dataset(dataset: Dataset) -> None:
    async with _locks[dataset]:
        _state[dataset] = "indexing"
        log.info("cognify start dataset=%s", dataset)
        try:
            await cognee.cognify(datasets=[dataset])
            log.info("cognify done dataset=%s", dataset)
        except Exception as e:
            log.exception("cognify failed dataset=%s", dataset)
            raise _wrap(e) from e
        finally:
            _state[dataset] = "idle"


async def _query(dataset: Dataset, q: str) -> str:
    if not q.strip():
        raise ValueError("empty query")
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
        raise CogneeServiceError(f"no material found for topic: {topic}")

    context_text = "\n\n---\n\n".join(
        str(c.get("text", "")) for c in chunks if isinstance(c, dict)
    )
    top_chunk = chunks[0] if isinstance(chunks[0], dict) else {}
    source_ref = None
    is_part_of = top_chunk.get("is_part_of") if isinstance(top_chunk, dict) else None
    if isinstance(is_part_of, dict):
        source_ref = is_part_of.get("name")

    system_prompt = (
        "You generate study quiz Q&A grounded strictly in the provided context. "
        "Return JSON matching the schema: {\"items\":[{\"question\":str,\"answer\":str},...]}. "
        f"Produce exactly {n} items. Questions must be answerable from the context; "
        "answers must be concise and factual."
    )
    user_prompt = f"Topic: {topic}\n\nContext:\n{context_text}"

    raw_items = await _quiz_llm_call_with_retry(system_prompt, user_prompt)

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


async def _quiz_llm_call_with_retry(system_prompt: str, user_prompt: str) -> list[dict]:
    """One-shot retry on malformed JSON — spec §9 risk mitigation."""
    last_exc: CogneeServiceError | None = None
    for attempt in (1, 2):
        try:
            return await _quiz_llm_call(system_prompt, user_prompt)
        except CogneeServiceError as e:
            if not e.retryable or attempt == 2:
                raise
            log.info("quiz retry after retryable error: %s", e)
            last_exc = e
    # Unreachable, but keeps type-checker happy.
    raise last_exc  # type: ignore[misc]


async def _quiz_llm_call(system_prompt: str, user_prompt: str) -> list[dict]:
    try:
        response = await litellm.acompletion(
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
        )
    except Exception as e:
        raise _wrap(e) from e

    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
        items = parsed["items"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("quiz JSON parse failed: %s; content=%r", e, content[:200])
        raise CogneeServiceError(
            "quiz generation returned invalid JSON", retryable=True
        ) from e

    if not isinstance(items, list) or not all(
        isinstance(i, dict) and "question" in i and "answer" in i for i in items
    ):
        log.warning("quiz items malformed: %r", str(items)[:200])
        raise CogneeServiceError(
            "quiz generation returned malformed items", retryable=True
        )
    return items
