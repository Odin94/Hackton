"""Demo-mode routes.

These endpoints exist for the live demo (TUM Reply Challenge):
- ``/demo/scripted-turn`` stores a prewritten (user, system) chat pair and
  pushes the system message over WebSocket. Bypasses the LLM so scripted
  beats are deterministic.
- ``/demo/system-message`` injects a prewritten system-authored chat message
  and pushes it over WebSocket. Used for the opening TumTum ping and any
  mid-demo canned reply.
- ``/demo/quiz-results`` persists a MC quiz and its score in one call so the
  next scene's LLM sees fresh data in its context window.

All three require a user bearer token (the presenter's session token).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from sqlalchemy import func, select

from agent.database import AsyncSessionLocal
from agent.models import Course, Deadline, Quiz, QuizResult, ScheduleEvent
from app.api_auth import require_bearer_user_id
from app.chat_models import ChatMessageResp, to_chat_message_resp
from app.chat_service import append_chat_message, serialize_chat_message
from app.connection_manager import manager

log = logging.getLogger(__name__)

router = APIRouter(tags=["demo"])


class ScriptedTurnReq(BaseModel):
    user_content: str = Field(min_length=1, max_length=4000)
    system_content: str = Field(min_length=1, max_length=4000)


class SystemMessageReq(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class QuizResultReq(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    topic: str = Field(min_length=1, max_length=256)
    estimated_duration_minutes: int = Field(ge=1, le=240, default=10)
    questions: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    correct_answers: int = Field(ge=0)
    false_answers: int = Field(ge=0)
    course_id: int | None = None


class ScriptedTurnResp(BaseModel):
    user_message: ChatMessageResp
    assistant_message: ChatMessageResp


class SystemMessageResp(BaseModel):
    message: ChatMessageResp


class QuizResultResp(BaseModel):
    quiz_id: int
    quiz_result_id: int


async def _push_chat_message(user_id: int, message) -> None:
    payload = {"type": "chat_message", "message": serialize_chat_message(message)}
    sent = await manager.send(user_id, payload)
    log.info(
        "demo ws push user_id=%d message_id=%d sequence=%d sent=%s",
        user_id,
        message.id,
        message.sequence_number,
        sent,
    )


@router.post(
    "/demo/scripted-turn",
    response_model=ScriptedTurnResp,
    summary="Store a prewritten user+system chat pair",
)
async def post_scripted_turn(
    req: ScriptedTurnReq,
    authorization: str | None = Header(default=None),
) -> ScriptedTurnResp:
    user_id = require_bearer_user_id(authorization)
    async with AsyncSessionLocal() as session:
        user_message = await append_chat_message(
            session, user_id=user_id, author="user", content=req.user_content
        )
        system_message = await append_chat_message(
            session, user_id=user_id, author="system", content=req.system_content
        )
        await session.commit()
        await session.refresh(user_message)
        await session.refresh(system_message)

    # Client sequences the two bubbles from the HTTP response so the user
    # message shows before the system reply. Pushing via WS here would race
    # the HTTP response and flip the order.
    return ScriptedTurnResp(
        user_message=to_chat_message_resp(user_message),
        assistant_message=to_chat_message_resp(system_message),
    )


@router.post(
    "/demo/system-message",
    response_model=SystemMessageResp,
    summary="Inject a system-authored chat message",
)
async def post_system_message(
    req: SystemMessageReq,
    authorization: str | None = Header(default=None),
) -> SystemMessageResp:
    user_id = require_bearer_user_id(authorization)
    async with AsyncSessionLocal() as session:
        message = await append_chat_message(
            session, user_id=user_id, author="system", content=req.content
        )
        await session.commit()
        await session.refresh(message)
    await _push_chat_message(user_id, message)
    return SystemMessageResp(message=to_chat_message_resp(message))


@router.post(
    "/demo/quiz-results",
    response_model=QuizResultResp,
    summary="Persist a completed quiz and its score",
)
async def post_quiz_results(
    req: QuizResultReq,
    authorization: str | None = Header(default=None),
) -> QuizResultResp:
    user_id = require_bearer_user_id(authorization)
    async with AsyncSessionLocal() as session:
        quiz = Quiz(
            user_id=user_id,
            course_id=req.course_id,
            title=req.title,
            topic=req.topic,
            estimated_duration_minutes=req.estimated_duration_minutes,
            questions=req.questions,
        )
        session.add(quiz)
        await session.flush()
        result = QuizResult(
            user_id=user_id,
            quiz_id=quiz.id,
            correct_answers=req.correct_answers,
            false_answers=req.false_answers,
            quiz_taken_datetime=datetime.now(UTC),
        )
        session.add(result)
        await session.flush()
        quiz_id = quiz.id
        result_id = result.id
        await session.commit()
    log.info(
        "demo quiz recorded user_id=%d quiz_id=%d result_id=%d correct=%d false=%d",
        user_id,
        quiz_id,
        result_id,
        req.correct_answers,
        req.false_answers,
    )
    return QuizResultResp(quiz_id=quiz_id, quiz_result_id=result_id)


class OverviewCourse(BaseModel):
    id: int
    name: str


class OverviewEvent(BaseModel):
    id: int
    course_name: str
    type: str
    name: str
    start_datetime: str
    end_datetime: str


class OverviewDeadline(BaseModel):
    id: int
    course_name: str
    name: str
    datetime: str


class OverviewQuizStat(BaseModel):
    total_taken: int
    average_percent: int


class OverviewResp(BaseModel):
    now: str
    courses: list[OverviewCourse]
    upcoming_events: list[OverviewEvent]
    upcoming_deadlines: list[OverviewDeadline]
    quiz: OverviewQuizStat


@router.get(
    "/demo/overview",
    response_model=OverviewResp,
    summary="Sidebar snapshot: courses, upcoming events, deadlines, quiz stats",
)
async def get_demo_overview(
    authorization: str | None = Header(default=None),
) -> OverviewResp:
    user_id = _require_user_id(authorization)
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        course_rows = (
            await session.execute(
                select(Course).where(Course.user_id == user_id).order_by(Course.id)
            )
        ).scalars().all()
        course_by_id = {c.id: c for c in course_rows}

        event_rows = (
            await session.execute(
                select(ScheduleEvent)
                .where(
                    ScheduleEvent.user_id == user_id,
                    ScheduleEvent.end_datetime >= now,
                )
                .order_by(ScheduleEvent.start_datetime)
                .limit(6)
            )
        ).scalars().all()

        deadline_rows = (
            await session.execute(
                select(Deadline)
                .where(
                    Deadline.user_id == user_id,
                    Deadline.datetime >= now,
                )
                .order_by(Deadline.datetime)
                .limit(6)
            )
        ).scalars().all()

        totals = (
            await session.execute(
                select(
                    func.count(QuizResult.id),
                    func.coalesce(func.sum(QuizResult.correct_answers), 0),
                    func.coalesce(func.sum(QuizResult.false_answers), 0),
                ).where(QuizResult.user_id == user_id)
            )
        ).one()

    taken = int(totals[0] or 0)
    correct = int(totals[1] or 0)
    wrong = int(totals[2] or 0)
    attempted = correct + wrong
    pct = round(100 * correct / attempted) if attempted else 0

    return OverviewResp(
        now=now.isoformat(),
        courses=[OverviewCourse(id=c.id, name=c.name) for c in course_rows],
        upcoming_events=[
            OverviewEvent(
                id=e.id,
                course_name=course_by_id[e.course_id].name if e.course_id in course_by_id else "",
                type=e.type,
                name=e.name,
                start_datetime=e.start_datetime.isoformat(),
                end_datetime=e.end_datetime.isoformat(),
            )
            for e in event_rows
        ],
        upcoming_deadlines=[
            OverviewDeadline(
                id=d.id,
                course_name=course_by_id[d.course_id].name if d.course_id in course_by_id else "",
                name=d.name,
                datetime=d.datetime.isoformat(),
            )
            for d in deadline_rows
        ],
        quiz=OverviewQuizStat(total_taken=taken, average_percent=pct),
    )
