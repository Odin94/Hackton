import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import cognee_service
from app.cognee_service import CogneeServiceError
from app.types import DiaryEntry, Material, QuizItem

router = APIRouter()


class CognifyReq(BaseModel):
    dataset: Literal["diary", "materials"]


class QueryReq(BaseModel):
    q: str


class QuizReq(BaseModel):
    topic: str
    n: int = 5


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, CogneeServiceError):
        status = 503 if exc.retryable else 500
        return HTTPException(status_code=status, detail=str(exc))
    return HTTPException(status_code=500, detail=f"unexpected: {exc}")


@router.post("/diary")
async def post_diary(entry: DiaryEntry) -> dict:
    try:
        await cognee_service.add_diary_entry(entry)
    except Exception as e:
        raise _map(e) from e
    return {"status": "queued"}


@router.post("/materials")
async def post_material(material: Material) -> dict:
    try:
        await cognee_service.add_material(material)
    except Exception as e:
        raise _map(e) from e
    return {"status": "queued"}


@router.post("/cognify")
async def post_cognify(req: CognifyReq) -> dict:
    asyncio.create_task(cognee_service.cognify_dataset(req.dataset))
    return {"status": "indexing"}


@router.post("/diary/query")
async def post_query_diary(req: QueryReq) -> dict:
    try:
        answer = await cognee_service.query_diary(req.q)
    except Exception as e:
        raise _map(e) from e
    return {"answer": answer}


@router.post("/materials/query")
async def post_query_materials(req: QueryReq) -> dict:
    try:
        answer = await cognee_service.query_materials(req.q)
    except Exception as e:
        raise _map(e) from e
    return {"answer": answer}


@router.post("/quiz")
async def post_quiz(req: QuizReq) -> dict:
    try:
        items: list[QuizItem] = await cognee_service.generate_quiz(req.topic, req.n)
    except Exception as e:
        raise _map(e) from e
    return {"items": [item.model_dump() for item in items]}


@router.get("/index-status")
async def get_index_status() -> dict:
    return cognee_service.index_status()
