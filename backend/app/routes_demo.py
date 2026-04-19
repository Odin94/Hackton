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

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy import func, select

from agent.database import AsyncSessionLocal
from agent.models import Course, Deadline, Quiz, QuizResult, ScheduleEvent
from app.api_auth import require_bearer_user_id
from app.chat_models import ChatMessageResp, to_chat_message_resp
from app.chat_service import append_chat_message, serialize_chat_message
from app.connection_manager import manager
from app.types import QuizItem

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


class QuizSummaryResp(BaseModel):
    id: int
    title: str
    topic: str
    course_name: str | None = None
    estimated_duration_minutes: int
    created_at: str
    question_count: int
    attempt_count: int
    average_percent: int | None = None
    best_percent: int | None = None
    latest_percent: int | None = None
    overall_average_percent: int
    underperformed: bool
    questions: list[QuizItem]


class QuizLibraryResp(BaseModel):
    overall_average_percent: int
    quizzes: list[QuizSummaryResp]


class RetakeQuizReq(BaseModel):
    correct_answers: int = Field(ge=0)
    false_answers: int = Field(ge=0)


class RetakeQuizResp(BaseModel):
    quiz_id: int
    quiz_result_id: int
    latest_percent: int
    average_percent: int
    attempt_count: int


def _normalize_quiz_question(
    question: Any,
    *,
    fallback_topic: str,
    fallback_source_ref: str | None,
) -> QuizItem:
    raw = question if isinstance(question, dict) else {}
    raw_options = raw.get("options")
    options = (
        list(raw_options[:4])
        if isinstance(raw_options, list) and len(raw_options) >= 4
        else [
            "Option A",
            "Option B",
            "Option C",
            "Option D",
        ]
    )
    while len(options) < 4:
        options.append(f"Option {chr(65 + len(options))}")

    try:
        correct_index = int(raw.get("correct_index", 0))
    except (TypeError, ValueError):
        correct_index = 0
    correct_index = max(0, min(3, correct_index))

    return QuizItem(
        question=str(raw.get("question") or "Untitled question"),
        answer=str(raw.get("answer") or "Review the lecture materials for the rationale."),
        options=[str(option) for option in options[:4]],
        correct_index=correct_index,
        topic=str(raw.get("topic") or fallback_topic),
        source_ref=(
            str(raw.get("source_ref"))
            if raw.get("source_ref") is not None
            else fallback_source_ref
        ),
    )


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
    user_id = require_bearer_user_id(authorization)
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


@router.get(
    "/demo/quizzes",
    response_model=QuizLibraryResp,
    summary="List all quizzes for the user with performance stats",
)
async def get_demo_quizzes(
    authorization: str | None = Header(default=None),
) -> QuizLibraryResp:
    user_id = require_bearer_user_id(authorization)
    async with AsyncSessionLocal() as session:
        quiz_rows = (
            await session.execute(
                select(Quiz).where(Quiz.user_id == user_id).order_by(Quiz.created_at.desc(), Quiz.id.desc())
            )
        ).scalars().all()
        result_rows = (
            await session.execute(
                select(QuizResult)
                .where(QuizResult.user_id == user_id)
                .order_by(QuizResult.quiz_taken_datetime.desc(), QuizResult.id.desc())
            )
        ).scalars().all()
        course_rows = (
            await session.execute(select(Course).where(Course.user_id == user_id))
        ).scalars().all()

    course_by_id = {course.id: course.name for course in course_rows}
    results_by_quiz: dict[int, list[QuizResult]] = {}
    overall_correct = 0
    overall_wrong = 0
    for result in result_rows:
        results_by_quiz.setdefault(result.quiz_id, []).append(result)
        overall_correct += result.correct_answers
        overall_wrong += result.false_answers

    overall_attempted = overall_correct + overall_wrong
    overall_average_percent = round(100 * overall_correct / overall_attempted) if overall_attempted else 0

    quizzes: list[QuizSummaryResp] = []
    for quiz in quiz_rows:
        attempts = results_by_quiz.get(quiz.id, [])
        scores = [
            round(100 * result.correct_answers / (result.correct_answers + result.false_answers))
            for result in attempts
            if (result.correct_answers + result.false_answers) > 0
        ]
        course_name = course_by_id.get(quiz.course_id) if quiz.course_id is not None else None
        normalized_questions = [
            _normalize_quiz_question(
                question,
                fallback_topic=quiz.topic,
                fallback_source_ref=course_name or quiz.topic,
            )
            for question in quiz.questions
        ]
        average_percent = round(sum(scores) / len(scores)) if scores else None
        best_percent = max(scores) if scores else None
        latest_percent = scores[0] if scores else None
        underperformed = average_percent is not None and average_percent < overall_average_percent
        quizzes.append(
            QuizSummaryResp(
                id=quiz.id,
                title=quiz.title,
                topic=quiz.topic,
                course_name=course_name,
                estimated_duration_minutes=quiz.estimated_duration_minutes,
                created_at=quiz.created_at.isoformat(),
                question_count=len(normalized_questions),
                attempt_count=len(scores),
                average_percent=average_percent,
                best_percent=best_percent,
                latest_percent=latest_percent,
                overall_average_percent=overall_average_percent,
                underperformed=underperformed,
                questions=normalized_questions,
            )
        )

    return QuizLibraryResp(
        overall_average_percent=overall_average_percent,
        quizzes=quizzes,
    )


@router.post(
    "/demo/quizzes/{quiz_id}/retake",
    response_model=RetakeQuizResp,
    summary="Store a retake result for an existing quiz",
)
async def post_demo_quiz_retake(
    quiz_id: int,
    req: RetakeQuizReq,
    authorization: str | None = Header(default=None),
) -> RetakeQuizResp:
    user_id = require_bearer_user_id(authorization)
    total_answers = req.correct_answers + req.false_answers
    if total_answers < 1:
        raise HTTPException(status_code=400, detail="retake result must contain at least one graded answer")

    async with AsyncSessionLocal() as session:
        quiz = await session.get(Quiz, quiz_id)
        if quiz is None or quiz.user_id != user_id:
            raise HTTPException(status_code=404, detail="quiz not found for user")

        result = QuizResult(
            user_id=user_id,
            quiz_id=quiz.id,
            correct_answers=req.correct_answers,
            false_answers=req.false_answers,
            quiz_taken_datetime=datetime.now(UTC),
        )
        session.add(result)
        await session.flush()

        result_rows = (
            await session.execute(
                select(QuizResult)
                .where(QuizResult.user_id == user_id, QuizResult.quiz_id == quiz.id)
                .order_by(QuizResult.quiz_taken_datetime.desc(), QuizResult.id.desc())
            )
        ).scalars().all()
        await session.commit()

    scores = [
        round(100 * item.correct_answers / (item.correct_answers + item.false_answers))
        for item in result_rows
        if (item.correct_answers + item.false_answers) > 0
    ]
    latest_percent = scores[0]
    average_percent = round(sum(scores) / len(scores))
    return RetakeQuizResp(
        quiz_id=quiz_id,
        quiz_result_id=result.id,
        latest_percent=latest_percent,
        average_percent=average_percent,
        attempt_count=len(scores),
    )
