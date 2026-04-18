"""HTTP routes for the cognee memory layer.

Three tags in the generated OpenAPI schema:
- `ingest`  — add data to a dataset (fast; no LLM).
- `index`   — cognify / index-status (slow; LLM-heavy).
- `query`   — diary / materials / quiz (retrieval + synthesis).
- `health`  — liveness.
"""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import cognee_service
from app.cognee_service import CogneeServiceError
from app.types import DiaryEntry, Material, QuizItem

log = logging.getLogger(__name__)

router = APIRouter()

_background_tasks: set[asyncio.Task] = set()


# --- request models ---------------------------------------------------------

class CognifyReq(BaseModel):
    dataset: Literal["diary", "materials"]


class QueryReq(BaseModel):
    q: str = Field(min_length=1, max_length=2000)


class QuizReq(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    n: int = Field(default=5, ge=1, le=20)


# --- response models --------------------------------------------------------

class StatusResp(BaseModel):
    status: Literal["ok", "queued", "indexing"]


class AnswerResp(BaseModel):
    answer: str


class QuizResp(BaseModel):
    items: list[QuizItem]


class IndexStatusResp(BaseModel):
    diary: Literal["idle", "indexing"]
    materials: Literal["idle", "indexing"]


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, CogneeServiceError):
        status = 503 if exc.retryable else 500
        return HTTPException(status_code=status, detail=str(exc))
    return HTTPException(status_code=500, detail=f"unexpected: {exc}")


@router.post("/diary", response_model=StatusResp, tags=["ingest"], summary="Add a diary entry")
async def post_diary(entry: DiaryEntry) -> StatusResp:
    """Append a dated journal entry to the diary dataset. Fast (no LLM call).

    Does not trigger cognify — call `/cognify` when you want the graph updated.
    """
    log.debug("POST /diary ts=%s tags=%s text_len=%d", entry.ts.isoformat(), entry.tags, len(entry.text))
    try:
        await cognee_service.add_diary_entry(entry)
    except Exception as e:
        log.warning("POST /diary failed ts=%s: %s", entry.ts.isoformat(), e)
        raise _map(e) from e
    log.debug("POST /diary → queued ts=%s", entry.ts.isoformat())
    return StatusResp(status="queued")


@router.post("/materials", response_model=StatusResp, tags=["ingest"], summary="Add a lecture material")
async def post_material(material: Material) -> StatusResp:
    """Append a lecture chunk to the materials dataset. Fast (no LLM call)."""
    log.debug("POST /materials source=%s course=%s text_len=%d", material.source, material.course, len(material.text))
    try:
        await cognee_service.add_material(material)
    except Exception as e:
        log.warning("POST /materials failed source=%s: %s", material.source, e)
        raise _map(e) from e
    log.debug("POST /materials → queued source=%s", material.source)
    return StatusResp(status="queued")


@router.post("/cognify", response_model=StatusResp, tags=["index"], summary="Index a dataset")
async def post_cognify(req: CognifyReq) -> StatusResp:
    """Kick off cognify for one dataset. Returns immediately; poll `/index-status`.

    Concurrent calls on the same dataset serialize via a per-dataset lock;
    cognify is idempotent so overlap is cheap.
    """
    log.debug("POST /cognify dataset=%s", req.dataset)
    task = asyncio.create_task(cognee_service.cognify_dataset(req.dataset))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    log.info("POST /cognify → background task spawned dataset=%s active_tasks=%d", req.dataset, len(_background_tasks))
    return StatusResp(status="indexing")


@router.get("/health", response_model=StatusResp, tags=["health"], summary="Liveness check")
async def get_health() -> StatusResp:
    """Returns `{status: "ok"}` once the app has started."""
    log.debug("GET /health")
    return StatusResp(status="ok")


@router.post("/diary/query", response_model=AnswerResp, tags=["query"], summary="Query diary graph")
async def post_query_diary(req: QueryReq) -> AnswerResp:
    """GRAPH_COMPLETION search over the diary dataset."""
    log.debug("POST /diary/query q_len=%d q=%.80r", len(req.q), req.q)
    try:
        answer = await cognee_service.query_diary(req.q)
    except Exception as e:
        log.warning("POST /diary/query failed q=%.80r: %s", req.q, e)
        raise _map(e) from e
    log.debug("POST /diary/query → answer_len=%d", len(answer))
    return AnswerResp(answer=answer)


@router.post("/materials/query", response_model=AnswerResp, tags=["query"], summary="Query materials graph")
async def post_query_materials(req: QueryReq) -> AnswerResp:
    """GRAPH_COMPLETION search over the materials dataset."""
    log.debug("POST /materials/query q_len=%d q=%.80r", len(req.q), req.q)
    try:
        answer = await cognee_service.query_materials(req.q)
    except Exception as e:
        log.warning("POST /materials/query failed q=%.80r: %s", req.q, e)
        raise _map(e) from e
    log.debug("POST /materials/query → answer_len=%d", len(answer))
    return AnswerResp(answer=answer)


@router.post("/quiz", response_model=QuizResp, tags=["query"], summary="Generate grounded quiz")
async def post_quiz(req: QuizReq) -> QuizResp:
    """Retrieve top-k chunks for `topic` from materials, ask the LLM for `n` Q&A
    items grounded in those chunks. `source_ref` on each item is the originating
    file when the chunk carries lineage.
    """
    log.debug("POST /quiz topic=%r n=%d", req.topic, req.n)
    try:
        items = await cognee_service.generate_quiz(req.topic, req.n)
    except Exception as e:
        log.warning("POST /quiz failed topic=%r n=%d: %s", req.topic, req.n, e)
        raise _map(e) from e
    log.debug("POST /quiz → %d items for topic=%r", len(items), req.topic)
    return QuizResp(items=items)


@router.get("/index-status", response_model=IndexStatusResp, tags=["index"], summary="Per-dataset indexing state")
async def get_index_status() -> IndexStatusResp:
    """Snapshot of the in-process state machine for each dataset."""
    status = cognee_service.index_status()
    log.debug("GET /index-status → %s", status)
    return IndexStatusResp(**status)
