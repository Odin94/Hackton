import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import cognee_service
from app.cognee_service import CogneeServiceError
from app.types import DiaryEntry, Material, QuizItem

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


@router.post("/diary", response_model=StatusResp)
async def post_diary(entry: DiaryEntry) -> StatusResp:
    try:
        await cognee_service.add_diary_entry(entry)
    except Exception as e:
        raise _map(e) from e
    return StatusResp(status="queued")


@router.post("/materials", response_model=StatusResp)
async def post_material(material: Material) -> StatusResp:
    try:
        await cognee_service.add_material(material)
    except Exception as e:
        raise _map(e) from e
    return StatusResp(status="queued")


@router.post("/cognify", response_model=StatusResp)
async def post_cognify(req: CognifyReq) -> StatusResp:
    task = asyncio.create_task(cognee_service.cognify_dataset(req.dataset))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return StatusResp(status="indexing")


@router.get("/health", response_model=StatusResp)
async def get_health() -> StatusResp:
    return StatusResp(status="ok")


@router.post("/diary/query", response_model=AnswerResp)
async def post_query_diary(req: QueryReq) -> AnswerResp:
    try:
        answer = await cognee_service.query_diary(req.q)
    except Exception as e:
        raise _map(e) from e
    return AnswerResp(answer=answer)


@router.post("/materials/query", response_model=AnswerResp)
async def post_query_materials(req: QueryReq) -> AnswerResp:
    try:
        answer = await cognee_service.query_materials(req.q)
    except Exception as e:
        raise _map(e) from e
    return AnswerResp(answer=answer)


@router.post("/quiz", response_model=QuizResp)
async def post_quiz(req: QuizReq) -> QuizResp:
    try:
        items = await cognee_service.generate_quiz(req.topic, req.n)
    except Exception as e:
        raise _map(e) from e
    return QuizResp(items=items)


@router.get("/index-status", response_model=IndexStatusResp)
async def get_index_status() -> IndexStatusResp:
    return IndexStatusResp(**cognee_service.index_status())
