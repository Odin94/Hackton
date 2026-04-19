"""Minimal chat service backed by SQLite history plus cognee retrieval."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import litellm
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent.database import AsyncSessionLocal
from agent.models import (
    ChatMessage,
    Course,
    Deadline,
    DemoConversationState,
    Notification,
    Quiz,
    QuizResult,
    ScheduleEvent,
    User,
)
from app import cognee_service
from app.config import settings

log = logging.getLogger(__name__)

_PROMPT_HISTORY_MESSAGES = 12
_COURSE_NOTIFICATION_MARKER = "What courses do you have?"
_SCHEDULE_NOTIFICATION_MARKER = "Please share the schedule for all of your courses"
_DEADLINE_NOTIFICATION_MARKER = "Please share any deadlines or exam dates"
_PROFILE_NOTIFICATION_MARKER = "What are your interests, and what are your goals for the future?"
_VALID_EVENT_TYPES = ("lecture", "tutorium", "study session")
_DEMO_STATUS_AWAITING_SLIDES = "awaiting_slides_progress"
_DEMO_STATUS_AWAITING_QUIZ = "awaiting_quiz_answers"
_DEMO_STATUS_AWAITING_ENERGY = "awaiting_energy_checkin"
_DEMO_STATUS_COMPLETE = "complete"
_DEMO_DEFAULT_AVERAGE_SCORE_PERCENT = 72
_DEMO_MINUTES_PER_QUESTION = 2

# Messages longer than this that don't match the skip pattern still get cognee.
_COGNEE_LONG_MESSAGE_THRESHOLD = 80
# Keywords that strongly signal the user wants to retrieve stored knowledge.
_COGNEE_TRIGGER_RE = re.compile(
    r"\b("
    r"explain|tell me|describe|summarize|recap|overview|"
    r"what is|what are|what do|what did|what does|what was|"
    r"how does|how do|how is|how are|how was|"
    r"why|"
    r"lecture|slide|slides|material|materials|concept|topic|theory|"
    r"algorithm|formula|theorem|definition|"
    r"diary|journal|habit|pattern|stress|energy|feel|feeling|mood|wellbeing|"
    r"remember|recall|know about|learned|studied|review"
    r")\b",
    re.IGNORECASE,
)


def _needs_cognee_context(message: str) -> bool:
    """Return True if cognee retrieval is likely needed for this message.

    Skips cognee for short conversational turns and onboarding replies where
    SQLite context is sufficient, saving 4-7s per turn.
    """
    stripped = message.strip()
    if _COGNEE_TRIGGER_RE.search(stripped):
        return True
    return len(stripped) > _COGNEE_LONG_MESSAGE_THRESHOLD


@dataclass
class ToolEvent:
    tool_name: str
    status: str
    detail: str
    surface_to_user: bool = True

_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_courses",
            "description": (
                "Add one or more course names for the user when they clearly list courses "
                "they are taking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "courses": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["courses"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_schedule_events",
            "description": (
                "Add new events to the user's schedule in SQLite when the user has clearly "
                "provided schedule details in chat. Use this only when you can normalize each event "
                "into explicit ISO-8601 start/end datetimes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": list(_VALID_EVENT_TYPES),
                                },
                                "course_id": {"type": "integer"},
                                "name": {"type": "string"},
                                "start_datetime": {"type": "string"},
                                "end_datetime": {"type": "string"},
                            },
                            "required": [
                                "type",
                                "course_id",
                                "name",
                                "start_datetime",
                                "end_datetime",
                            ],
                        },
                    }
                },
                "required": ["events"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_deadlines",
            "description": (
                "Add course-linked deadlines or exam dates when the user clearly provides "
                "a course, a name, and a specific datetime."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "deadlines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "course_id": {"type": "integer"},
                                "name": {"type": "string"},
                                "datetime": {"type": "string"},
                            },
                            "required": ["course_id", "name", "datetime"],
                        },
                    }
                },
                "required": ["deadlines"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_user_profile",
            "description": (
                "Save the user's broader interests and future goals when they clearly share "
                "both during onboarding or a later profile update."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "interests": {"type": "string"},
                    "future_goals": {"type": "string"},
                },
                "required": ["interests", "future_goals"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_demo_quiz",
            "description": (
                "During the scripted demo flow, generate and store a grounded quiz from the "
                "materials dataset for the active demo course. Use this instead of inventing a quiz."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "coverage_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                    "question_count": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["coverage_percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_demo_quiz_result",
            "description": (
                "Persist the graded result for the active demo quiz after comparing the user's "
                "answers against the stored answer key."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "correct_answers": {"type": "integer", "minimum": 0},
                    "false_answers": {"type": "integer", "minimum": 0},
                },
                "required": ["correct_answers", "false_answers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_demo_flow",
            "description": (
                "Mark the scripted demo flow complete after giving the final energy-based recommendation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": ["library_study_session", "rest_and_recover"],
                    }
                },
                "required": ["outcome"],
            },
        },
    },
]


def _preview(value: str, limit: int = 160) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _usage_from_response(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if isinstance(usage, dict):
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _format_tool_feedback(tool_events: list[ToolEvent]) -> str:
    lines: list[str] = []
    for event in tool_events:
        if not event.surface_to_user:
            continue
        label = event.tool_name.replace("_", " ")
        if event.status == "success":
            lines.append(f"{label}: {event.detail}")
        else:
            lines.append(f"{label} failed: {event.detail}")
    return "\n".join(lines)


def _append_usage_footer(content: str, usage_totals: dict[str, int]) -> str:
    if usage_totals["total_tokens"] <= 0:
        return content
    footer = (
        f"[tokens: prompt={usage_totals['prompt_tokens']}, "
        f"completion={usage_totals['completion_tokens']}, total={usage_totals['total_tokens']}]"
    )
    return f"{content}\n\n{footer}"


async def list_chat_messages(user_id: int) -> list[ChatMessage]:
    log.debug("chat.list_chat_messages start user_id=%d", user_id)
    async with AsyncSessionLocal() as session:
        messages = await _list_chat_messages(session, user_id)
    log.debug("chat.list_chat_messages done user_id=%d count=%d", user_id, len(messages))
    return messages


def serialize_chat_message(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "user_id": message.user_id,
        "timestamp": message.timestamp.isoformat(),
        "author": message.author,
        "sequence_number": message.sequence_number,
        "content": message.content,
    }


async def append_chat_message(
    session: AsyncSession,
    *,
    user_id: int,
    author: str,
    content: str,
    timestamp: datetime | None = None,
) -> ChatMessage:
    next_sequence = await _next_sequence_number(session, user_id)
    log.debug(
        "chat.append_chat_message user_id=%d author=%s next_sequence=%d content_preview=%r",
        user_id,
        author,
        next_sequence,
        _preview(content),
    )
    message = ChatMessage(
        user_id=user_id,
        timestamp=timestamp or datetime.now(UTC),
        author=author,
        sequence_number=next_sequence,
        content=content.strip(),
    )
    session.add(message)
    await session.flush()
    return message


async def create_chat_reply(user_id: int, content: str) -> tuple[ChatMessage, ChatMessage]:
    cleaned = content.strip()
    if not cleaned:
        raise ValueError("message cannot be empty")
    log.debug(
        "chat.create_chat_reply start user_id=%d content_len=%d preview=%r",
        user_id,
        len(cleaned),
        _preview(cleaned),
    )

    async with AsyncSessionLocal() as session:
        user_message = await append_chat_message(
            session, user_id=user_id, author="user", content=cleaned
        )
        log.debug(
            "chat.create_chat_reply user_message_flushed user_id=%d message_id=%d sequence=%d",
            user_id,
            user_message.id,
            user_message.sequence_number,
        )
        await session.commit()
        await session.refresh(user_message)
        log.debug(
            "chat.create_chat_reply user_message_committed user_id=%d message_id=%d sequence=%d",
            user_id,
            user_message.id,
            user_message.sequence_number,
        )

    try:
        async with AsyncSessionLocal() as session:
            response_text = await _generate_chat_response(session, user_id, cleaned)
            system_message = await append_chat_message(
                session, user_id=user_id, author="system", content=response_text
            )
            await session.commit()
            await session.refresh(system_message)
    except Exception:
        log.exception(
            "chat.create_chat_reply assistant_generation_failed user_id=%d user_message_id=%d",
            user_id,
            user_message.id,
        )
        raise

    log.debug(
        "chat.create_chat_reply committed user_id=%d user_message_id=%d system_message_id=%d system_sequence=%d system_preview=%r",
        user_id,
        user_message.id,
        system_message.id,
        system_message.sequence_number,
        _preview(system_message.content),
    )
    return user_message, system_message


async def deliver_due_notifications_as_chat_messages(user_id: int) -> list[ChatMessage]:
    log.debug("chat.deliver_due_notifications start user_id=%d", user_id)
    async with AsyncSessionLocal() as session:
        now = datetime.now(UTC)
        due_notifications = (
            (
                await session.execute(
                    select(Notification)
                    .where(
                        Notification.user_id == user_id,
                        Notification.status == "pending",
                        Notification.target_datetime <= now,
                    )
                    .order_by(Notification.target_datetime.asc(), Notification.id.asc())
                )
            )
            .scalars()
            .all()
        )
        log.debug(
            "chat.deliver_due_notifications fetched user_id=%d due_count=%d now=%s notification_ids=%s",
            user_id,
            len(due_notifications),
            now.isoformat(),
            [notification.id for notification in due_notifications],
        )

        delivered_messages: list[ChatMessage] = []
        for notification in due_notifications:
            log.debug(
                "chat.deliver_due_notifications converting notification_id=%d user_id=%d target=%s content_preview=%r",
                notification.id,
                user_id,
                notification.target_datetime.isoformat(),
                _preview(notification.content),
            )
            message = await append_chat_message(
                session,
                user_id=user_id,
                author="system",
                content=notification.content,
            )
            notification.status = "complete"
            delivered_messages.append(message)

        await session.commit()
        for message in delivered_messages:
            await session.refresh(message)
        log.debug(
            "chat.deliver_due_notifications committed user_id=%d delivered_message_ids=%s sequences=%s",
            user_id,
            [message.id for message in delivered_messages],
            [message.sequence_number for message in delivered_messages],
        )
        return delivered_messages


async def activate_demo_conversation(
    user_id: int,
    *,
    course_name: str,
    average_score_percent: int = _DEMO_DEFAULT_AVERAGE_SCORE_PERCENT,
) -> tuple[Notification, list[ChatMessage]]:
    cleaned_course_name = course_name.strip()
    if not cleaned_course_name:
        raise ValueError("course_name cannot be empty")

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.status == "pending",
                Notification.quiz_id.is_(None),
                (
                    Notification.content.contains(_COURSE_NOTIFICATION_MARKER)
                    | Notification.content.contains(_SCHEDULE_NOTIFICATION_MARKER)
                    | Notification.content.contains(_DEADLINE_NOTIFICATION_MARKER)
                    | Notification.content.contains(_PROFILE_NOTIFICATION_MARKER)
                    | Notification.content.contains("Hey you finished your ")
                ),
            )
            .values(status="complete")
        )
        state = await session.scalar(
            select(DemoConversationState).where(DemoConversationState.user_id == user_id)
        )
        if state is None:
            state = DemoConversationState(
                user_id=user_id,
                status=_DEMO_STATUS_AWAITING_SLIDES,
                course_name=cleaned_course_name,
                coverage_percent=None,
                quiz_id=None,
                average_score_percent=average_score_percent,
            )
            session.add(state)
        else:
            state.status = _DEMO_STATUS_AWAITING_SLIDES
            state.course_name = cleaned_course_name
            state.coverage_percent = None
            state.quiz_id = None
            state.average_score_percent = average_score_percent

        notification = Notification(
            user_id=user_id,
            status="pending",
            target_datetime=datetime.now(UTC),
            content=(
                f"Hey you finished your {cleaned_course_name} lecture 20 minutes ago! "
                "Did you get through today's slides?"
            ),
            quiz_id=None,
        )
        session.add(notification)
        await session.commit()
        await session.refresh(notification)

    delivered = await deliver_due_notifications_as_chat_messages(user_id)
    return notification, delivered


async def _demo_state_for_user(
    session: AsyncSession,
    user_id: int,
) -> DemoConversationState | None:
    return await session.scalar(
        select(DemoConversationState).where(DemoConversationState.user_id == user_id)
    )


def _demo_quiz_topic_for_course(course_name: str) -> str:
    lowered = course_name.casefold()
    if "machine learning" in lowered or lowered == "ml":
        return "transformers"
    return course_name


def _demo_status_label(status: str) -> str:
    mapping = {
        _DEMO_STATUS_AWAITING_SLIDES: "awaiting slides progress update",
        _DEMO_STATUS_AWAITING_QUIZ: "awaiting quiz answers",
        _DEMO_STATUS_AWAITING_ENERGY: "awaiting energy check-in",
        _DEMO_STATUS_COMPLETE: "complete",
    }
    return mapping.get(status, status)


def _render_quiz_questions(questions: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"{index}. {str(question.get('question', '')).strip()}"
        for index, question in enumerate(questions, start=1)
    )


async def _create_demo_quiz(
    session: AsyncSession,
    user_id: int,
    *,
    coverage_percent: int,
    question_count: int,
) -> tuple[Quiz, DemoConversationState, str]:
    state = await _demo_state_for_user(session, user_id)
    if state is None:
        raise ValueError("demo mode is not active for this user")
    if state.status not in {_DEMO_STATUS_AWAITING_SLIDES, _DEMO_STATUS_AWAITING_QUIZ}:
        raise ValueError("demo quiz generation is not expected at the current demo stage")

    quiz_topic = _demo_quiz_topic_for_course(state.course_name)
    items = await cognee_service.generate_quiz(quiz_topic, n=question_count)
    quiz = Quiz(
        user_id=user_id,
        title=f"Demo quiz: {state.course_name}",
        topic=f"{state.course_name} first {coverage_percent}% of slides",
        estimated_duration_minutes=max(1, len(items)) * _DEMO_MINUTES_PER_QUESTION,
        questions=[item.model_dump() for item in items],
    )
    session.add(quiz)
    await session.flush()

    state.coverage_percent = coverage_percent
    state.quiz_id = quiz.id
    state.status = _DEMO_STATUS_AWAITING_QUIZ
    await session.flush()

    question_block = _render_quiz_questions(quiz.questions)
    detail = (
        f"Stored demo quiz quiz_id={quiz.id} for course={state.course_name!r}, "
        f"coverage={coverage_percent}%, topic={quiz_topic!r}. "
        "Present the quiz as open-ended questions only. Do not invent multiple-choice options. "
        "After the questions, instruct the user to reply with numbered free-text answers like "
        "`1. ... 2. ... 3. ...`.\n"
        f"Questions:\n{question_block}"
    )
    return quiz, state, detail


async def _record_demo_quiz_result(
    session: AsyncSession,
    user_id: int,
    *,
    correct_answers: int,
    false_answers: int,
) -> tuple[DemoConversationState, str]:
    state = await _demo_state_for_user(session, user_id)
    if state is None or state.quiz_id is None:
        raise ValueError("no active demo quiz exists for this user")
    if state.status != _DEMO_STATUS_AWAITING_QUIZ:
        raise ValueError("demo quiz results are not expected at the current demo stage")

    if correct_answers < 0 or false_answers < 0:
        raise ValueError("quiz result counts cannot be negative")
    total_answers = correct_answers + false_answers
    if total_answers < 1:
        raise ValueError("quiz result must contain at least one graded answer")

    score_percent = round((correct_answers / total_answers) * 100)
    previous_average = await session.scalar(
        select(
            func.avg(
                (QuizResult.correct_answers * 100.0)
                / (QuizResult.correct_answers + QuizResult.false_answers)
            )
        )
        .where(QuizResult.user_id == user_id)
        .where(QuizResult.quiz_id != state.quiz_id)
    )
    average_score = (
        int(round(previous_average))
        if previous_average is not None
        else state.average_score_percent
    )
    comparison = "better" if score_percent >= average_score else "worse"

    result = QuizResult(
        user_id=user_id,
        quiz_id=state.quiz_id,
        correct_answers=correct_answers,
        false_answers=false_answers,
        quiz_taken_datetime=datetime.now(UTC),
    )
    session.add(result)
    state.status = _DEMO_STATUS_AWAITING_ENERGY
    await session.flush()

    detail = (
        f"Recorded demo quiz result for quiz_id={state.quiz_id}: "
        f"correct={correct_answers}, false={false_answers}, score={score_percent}%. "
        f"Historical average before this quiz={average_score}%, so this performance is {comparison}."
    )
    return state, detail


async def _complete_demo_flow(
    session: AsyncSession,
    user_id: int,
    *,
    outcome: str,
    demo_status_at_turn_start: str | None,
) -> str:
    state = await _demo_state_for_user(session, user_id)
    if state is None:
        raise ValueError("demo mode is not active for this user")
    if demo_status_at_turn_start != _DEMO_STATUS_AWAITING_ENERGY:
        raise ValueError(
            "demo flow can only be completed on a turn that started after the energy question"
        )
    state.status = _DEMO_STATUS_COMPLETE
    await session.flush()
    return f"Marked demo flow complete with outcome={outcome}."


def _build_demo_prompt(state: DemoConversationState) -> str:
    quiz_topic = _demo_quiz_topic_for_course(state.course_name)
    return (
        "A special scripted demo flow is active. Follow this conversation arc while still "
        "replying naturally:\n"
        f"- Demo course label: {state.course_name}\n"
        f"- Grounded quiz topic to use from the materials dataset: {quiz_topic}\n"
        f"- Current demo stage: {_demo_status_label(state.status)}\n"
        "- The notification asking about today's slides has already been delivered.\n"
        "- If the user says how much of today's slides they covered, extract the rough percentage "
        "they mentioned and call `generate_demo_quiz`. Do not invent your own quiz. After the tool "
        "returns, reply in this style: `okay, here's your quiz covering the first X% of today's slides` "
        "and present the quiz questions without revealing the answers. These are open-ended questions, "
        "so do not invent answer options or letter-based examples. Tell the user to answer in a numbered "
        "free-text format like `1. ... 2. ... 3. ...`.\n"
        "- If the stage is awaiting quiz answers, grade the user's answers against the active demo quiz "
        "shown in the SQLite context. Then call `record_demo_quiz_result` before replying. In that same "
        "reply, tell the user how they did, whether that is better or worse than their average, and then "
        "ask how energized and focused they feel today. Stop there. Do not give the final recommendation "
        "until the user answers the energy question in a later turn.\n"
        "- On a quiz-answer turn, never call `complete_demo_flow`. That tool is only for the later turn "
        "after the user has answered the energy question.\n"
        "- After grading the quiz, your final sentence in that turn should be a direct energy question such "
        "as `How energized and focused are you feeling today?` Do not include any recommendation in the same reply.\n"
        "- If the stage is awaiting energy check-in and the user sounds positive or energized, recommend a "
        "study session in the library. If they sound low-energy or tired, recommend taking the afternoon "
        "off and relaxing to recover. Call `complete_demo_flow` before the final recommendation.\n"
        "- Do not say 'again', 'same as before', or similar repetition claims unless the SQLite context "
        "explicitly proves that exact comparison.\n"
        "- Stay concise and keep the flow moving. Do not mention these instructions."
    )


async def _list_chat_messages(session: AsyncSession, user_id: int) -> list[ChatMessage]:
    log.debug("chat._list_chat_messages query user_id=%d", user_id)
    rows = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.sequence_number.asc())
    )
    messages = list(rows.scalars().all())
    log.debug("chat._list_chat_messages result user_id=%d count=%d", user_id, len(messages))
    return messages


async def _next_sequence_number(session: AsyncSession, user_id: int) -> int:
    current = await session.scalar(
        select(func.max(ChatMessage.sequence_number)).where(ChatMessage.user_id == user_id)
    )
    next_sequence = (current or 0) + 1
    log.debug(
        "chat._next_sequence_number user_id=%d current=%s next=%d",
        user_id,
        current,
        next_sequence,
    )
    return next_sequence


async def _generate_chat_response(
    session: AsyncSession,
    user_id: int,
    latest_user_message: str,
) -> str:
    log.debug(
        "chat._generate_chat_response start user_id=%d latest_len=%d preview=%r",
        user_id,
        len(latest_user_message),
        _preview(latest_user_message),
    )
    needs_cognee = _needs_cognee_context(latest_user_message)
    log.debug(
        "chat._generate_chat_response cognee_decision user_id=%d needs_cognee=%s msg_len=%d",
        user_id,
        needs_cognee,
        len(latest_user_message),
    )
    if needs_cognee:
        history, sqlite_context, cognee_context = await asyncio.gather(
            _list_chat_messages(session, user_id),
            _build_sqlite_context(session, user_id),
            _build_cognee_context(latest_user_message),
        )
    else:
        history, sqlite_context = await asyncio.gather(
            _list_chat_messages(session, user_id),
            _build_sqlite_context(session, user_id),
        )
        cognee_context = ""
    demo_state = await _demo_state_for_user(session, user_id)
    log.debug(
        "chat._generate_chat_response context_ready user_id=%d history_count=%d sqlite_len=%d cognee_len=%d",
        user_id,
        len(history),
        len(sqlite_context),
        len(cognee_context),
    )
    prompt_history = history[-_PROMPT_HISTORY_MESSAGES:]
    history_lines = "\n".join(
        f"{message.author.upper()}: {message.content}" for message in prompt_history
    ) or "(no previous chat history)"

    system_prompt = (
        "You are the study assistant for this app. Answer using only the supplied "
        "SQLite app data, stored chat history, and cognee retrieval results. "
        "If the answer is not supported by that context, say you could not find "
        "it in the stored knowledge. Be concise, helpful, and direct. "
        "Treat 'system' chat messages as prior assistant replies.\n\n"
        "If diary retrieval shows personal study or wellbeing patterns, you may "
        "use them for personalized advice, but keep the advice grounded in the "
        "retrieved diary text. For this demo app, treat entries retrieved from the "
        "diary dataset as the user's own personal diary entries.\n\n"
        "If the user sends a brief acknowledgement like 'thanks', 'ok thanks', or "
        "'got it', reply naturally and briefly. Do not claim missing knowledge or "
        "ask for more diary data unless the user explicitly asks for an analysis that "
        "needs it.\n\n"
        "You can use three tools during onboarding and later updates:\n"
        "- add_courses: save courses the user is taking.\n"
        "- add_schedule_events: save course-linked schedule events.\n"
        "- add_deadlines: save course-linked deadlines or exam dates.\n\n"
        "- save_user_profile: save the user's interests and future goals.\n\n"
        "Onboarding order matters:\n"
        "1. If the user has no saved courses yet, ask 'What courses do you have?' and use "
        "add_courses when they clearly name them. Confirm the courses you added by name.\n"
        "2. After courses exist, ask for the schedule for all courses and use add_schedule_events "
        "only when each event can be normalized into explicit ISO-8601 start/end datetimes and "
        "linked to a saved course_id. If the user only gives schedules for some saved courses, "
        "save those and ask one more time for the missing course names.\n"
        "3. After every course has at least one schedule event, ask for deadlines or exam dates and "
        "use add_deadlines only when each item has a saved course_id plus a specific ISO-8601 datetime. "
        "If some courses are still missing deadlines, ask specifically for those.\n"
        "4. After deadlines are covered, ask for the user's interests and what their goals for the "
        "future are. Use save_user_profile only when both are clear. If they answer only one part, "
        "ask a short follow-up for the missing part.\n\n"
        "Do not invent courses, course_ids, datetimes, or deadlines. If required details are "
        "ambiguous or missing, ask a short clarifying question instead."
    )
    if demo_state is not None and demo_state.status != _DEMO_STATUS_COMPLETE:
        system_prompt = f"{system_prompt}\n\n{_build_demo_prompt(demo_state)}"
    user_prompt = (
        f"Current UTC time: {datetime.now(UTC).isoformat()}\n"
        f"User ID: {user_id}\n\n"
        f"Latest user message:\n{latest_user_message}\n\n"
        f"Recent chat history:\n{history_lines}\n\n"
        f"SQLite application context:\n{sqlite_context}\n\n"
        f"Cognee context:\n{cognee_context}\n"
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_events: list[ToolEvent] = []
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for turn in range(1, 5):
        current_demo_state = await _demo_state_for_user(session, user_id)
        demo_status_at_turn_start = (
            current_demo_state.status if current_demo_state is not None else None
        )
        log.debug(
            "chat._generate_chat_response llm_turn_start user_id=%d turn=%d messages=%d latest_preview=%r",
            user_id,
            turn,
            len(messages),
            _preview(latest_user_message),
        )
        response = await _chat_completion(messages)
        usage = _usage_from_response(response)
        for key, value in usage.items():
            usage_totals[key] += value
        log.debug(
            "chat._generate_chat_response usage user_id=%d turn=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d running_total=%d",
            user_id,
            turn,
            usage["prompt_tokens"],
            usage["completion_tokens"],
            usage["total_tokens"],
            usage_totals["total_tokens"],
        )
        assistant_message = response.choices[0].message
        messages.append(assistant_message.model_dump(exclude_none=True))
        assistant_content = getattr(assistant_message, "content", "") or ""
        log.debug(
            "chat._generate_chat_response llm_turn_response user_id=%d turn=%d content_len=%d content_preview=%r",
            user_id,
            turn,
            len(assistant_content),
            _preview(assistant_content),
        )

        tool_calls = getattr(assistant_message, "tool_calls", None) or []
        if not tool_calls:
            content = getattr(assistant_message, "content", "") or ""
            cleaned = content.strip()
            if not cleaned:
                raise cognee_service.MalformedLLMResponseError(
                    "chat generation returned empty content"
                )
            tool_feedback = _format_tool_feedback(tool_events)
            if tool_feedback:
                cleaned = f"{tool_feedback}\n\n{cleaned}"
            cleaned = _append_usage_footer(cleaned, usage_totals)
            log.debug(
                "chat._generate_chat_response done user_id=%d turn=%d final_preview=%r",
                user_id,
                turn,
                _preview(cleaned),
            )
            return cleaned

        for tool_call in tool_calls:
            arguments = tool_call.function.arguments
            arguments_text = arguments if isinstance(arguments, str) else json.dumps(arguments)
            log.debug(
                "chat._generate_chat_response tool_call user_id=%d turn=%d tool=%s tool_call_id=%s args_preview=%r",
                user_id,
                turn,
                tool_call.function.name,
                tool_call.id,
                _preview(arguments_text),
            )
            try:
                args = json.loads(arguments_text)
            except json.JSONDecodeError as e:
                log.warning(
                    "chat._generate_chat_response tool_args_parse_error user_id=%d turn=%d tool=%s error=%s raw_args=%r",
                    user_id,
                    turn,
                    tool_call.function.name,
                    e,
                    arguments_text,
                )
                result = (
                    "Failed to parse tool arguments. Ask the user to resend the schedule "
                    "with explicit days, dates, and start/end times."
                )
                tool_events.append(
                    ToolEvent(
                        tool_name=tool_call.function.name,
                        status="error",
                        detail="invalid tool arguments",
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
                continue

            result, tool_event = await _dispatch_tool_call(
                session,
                user_id,
                tool_call.function.name,
                args,
                demo_status_at_turn_start=demo_status_at_turn_start,
            )
            tool_events.append(tool_event)
            log.debug(
                "chat._generate_chat_response tool_result user_id=%d turn=%d tool=%s result_preview=%r",
                user_id,
                turn,
                tool_call.function.name,
                _preview(result),
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    if tool_events:
        fallback = _format_tool_feedback(tool_events) or "The requested tool actions completed."
        fallback = _append_usage_footer(fallback, usage_totals)
        log.warning(
            "chat._generate_chat_response fallback_after_tool_calls user_id=%d tool_event_count=%d fallback_preview=%r",
            user_id,
            len(tool_events),
            _preview(fallback),
        )
        return fallback

    raise cognee_service.MalformedLLMResponseError("chat generation did not finish cleanly")


async def _chat_completion(messages: list[dict[str, Any]]):
    last_user_message = next(
        (message.get("content", "") for message in reversed(messages) if message.get("role") == "user"),
        "",
    )
    log.debug(
        "chat._chat_completion request model=%s message_count=%d last_user_preview=%r tool_names=%s",
        settings.llm_model,
        len(messages),
        _preview(str(last_user_message)),
        [tool["function"]["name"] for tool in _CHAT_TOOLS],
    )
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                messages=messages,
                tools=_CHAT_TOOLS,
                tool_choice="auto",
            ),
            timeout=settings.llm_call_timeout_seconds,
        )
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        log.debug(
            "chat._chat_completion response content_len=%d tool_call_count=%d tool_names=%s",
            len(getattr(message, "content", "") or ""),
            len(tool_calls),
            [tool_call.function.name for tool_call in tool_calls],
        )
        return response
    except TimeoutError as e:
        log.warning("chat._chat_completion timeout after=%ss", settings.llm_call_timeout_seconds)
        raise cognee_service.LLMTimeoutError(
            f"LLM call exceeded {settings.llm_call_timeout_seconds}s timeout"
        ) from e
    except Exception as e:
        log.warning("chat._chat_completion upstream_error=%s", e, exc_info=True)
        raise cognee_service._wrap(e) from e


async def _dispatch_tool_call(
    session: AsyncSession,
    user_id: int,
    tool_name: str,
    args: dict[str, Any],
    *,
    demo_status_at_turn_start: str | None = None,
) -> tuple[str, ToolEvent]:
    log.debug(
        "chat._dispatch_tool_call user_id=%d tool=%s arg_keys=%s",
        user_id,
        tool_name,
        sorted(args.keys()),
    )
    if tool_name == "add_courses":
        try:
            courses = await _add_courses(session, user_id, args.get("courses", []))
            names = ", ".join(f"{course.name} (id={course.id})" for course in courses)
            detail = f"added {len(courses)} course(s): {names}"
            return f"Added {len(courses)} course(s): {names}.", ToolEvent(
                tool_name=tool_name,
                status="success",
                detail=detail,
            )
        except ValueError as e:
            log.warning(
                "chat._dispatch_tool_call validation_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            detail = str(e)
            return (
                "Failed to add courses because the provided course names were invalid. "
                f"Error: {e}. Ask the user to resend the course names clearly."
            ), ToolEvent(tool_name=tool_name, status="error", detail=detail)

    if tool_name == "add_schedule_events":
        try:
            events, missing_courses = await _add_schedule_events(session, user_id, args.get("events", []))
            names = ", ".join(event.name for event in events)
            detail = f"added {len(events)} event(s): {names}"
            if missing_courses:
                detail += f"; still missing schedules for: {', '.join(missing_courses)}"
            return f"Added {len(events)} event(s) to the user's schedule.", ToolEvent(
                tool_name=tool_name,
                status="success",
                detail=detail,
            )
        except ValueError as e:
            log.warning(
                "chat._dispatch_tool_call validation_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            detail = str(e)
            return (
                "Failed to add schedule events because the provided event details were invalid: "
                f"{e}. Ask the user for a clearer schedule with explicit days and times."
            ), ToolEvent(tool_name=tool_name, status="error", detail=detail)

    if tool_name == "add_deadlines":
        try:
            deadlines, missing_courses = await _add_deadlines(session, user_id, args.get("deadlines", []))
            names = ", ".join(deadline.name for deadline in deadlines)
            detail = f"added {len(deadlines)} deadline(s): {names}"
            if missing_courses:
                detail += f"; still missing deadlines for: {', '.join(missing_courses)}"
            return f"Added {len(deadlines)} deadline(s) for the user.", ToolEvent(
                tool_name=tool_name,
                status="success",
                detail=detail,
            )
        except ValueError as e:
            log.warning(
                "chat._dispatch_tool_call validation_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            detail = str(e)
            return (
                "Failed to add deadlines because the provided deadline details were invalid: "
                f"{e}. Ask the user for explicit course names and exact dates or times."
            ), ToolEvent(tool_name=tool_name, status="error", detail=detail)

    if tool_name == "save_user_profile":
        try:
            user = await _save_user_profile(
                session,
                user_id,
                interests=str(args["interests"]),
                future_goals=str(args["future_goals"]),
            )
            detail = (
                f"saved interests={user.interests!r}; "
                f"saved future goals={user.future_goals!r}"
            )
            return "Saved the user's interests and future goals.", ToolEvent(
                tool_name=tool_name,
                status="success",
                detail=detail,
            )
        except ValueError as e:
            log.warning(
                "chat._dispatch_tool_call validation_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            detail = str(e)
            return (
                "Failed to save the user's interests and future goals because the provided "
                f"profile details were invalid: {e}. Ask the user for both clearly."
            ), ToolEvent(tool_name=tool_name, status="error", detail=detail)

    if tool_name == "generate_demo_quiz":
        try:
            question_count = int(args.get("question_count", 3))
            if question_count < 1:
                raise ValueError("question_count must be >= 1")
            coverage_percent = int(args["coverage_percent"])
            _, _, detail = await _create_demo_quiz(
                session,
                user_id,
                coverage_percent=coverage_percent,
                question_count=question_count,
            )
            return detail, ToolEvent(
                tool_name=tool_name,
                status="success",
                detail="generated grounded demo quiz",
                surface_to_user=False,
            )
        except (ValueError, cognee_service.CogneeServiceError) as e:
            log.warning(
                "chat._dispatch_tool_call demo_quiz_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            return (
                "Failed to generate the grounded demo quiz. Ask the user to try again in a moment "
                "or use a clearer lecture topic."
            ), ToolEvent(
                tool_name=tool_name,
                status="error",
                detail=str(e),
                surface_to_user=False,
            )

    if tool_name == "record_demo_quiz_result":
        try:
            _, detail = await _record_demo_quiz_result(
                session,
                user_id,
                correct_answers=int(args["correct_answers"]),
                false_answers=int(args["false_answers"]),
            )
            return detail, ToolEvent(
                tool_name=tool_name,
                status="success",
                detail="recorded demo quiz result",
                surface_to_user=False,
            )
        except ValueError as e:
            log.warning(
                "chat._dispatch_tool_call demo_result_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            return (
                "Failed to record the demo quiz result. Re-check the grading and try again."
            ), ToolEvent(
                tool_name=tool_name,
                status="error",
                detail=str(e),
                surface_to_user=False,
            )

    if tool_name == "complete_demo_flow":
        try:
            detail = await _complete_demo_flow(
                session,
                user_id,
                outcome=str(args["outcome"]).strip(),
                demo_status_at_turn_start=demo_status_at_turn_start,
            )
            return detail, ToolEvent(
                tool_name=tool_name,
                status="success",
                detail="completed demo flow",
                surface_to_user=False,
            )
        except ValueError as e:
            log.warning(
                "chat._dispatch_tool_call demo_complete_error user_id=%d tool=%s error=%s args=%r",
                user_id,
                tool_name,
                e,
                args,
            )
            return (
                "Failed to complete the demo flow state. Ask the user how they feel first, then "
                "wait for that answer before giving the final recommendation."
            ), ToolEvent(
                tool_name=tool_name,
                status="error",
                detail=str(e),
                surface_to_user=False,
            )

    else:
        log.warning("chat._dispatch_tool_call unknown_tool user_id=%d tool=%s", user_id, tool_name)
        detail = f"Unknown tool '{tool_name}'."
        return detail, ToolEvent(tool_name=tool_name, status="error", detail=detail)
    
    
async def _upsert_followup_notification(
    session: AsyncSession,
    *,
    user_id: int,
    marker: str,
    content: str,
) -> None:
    existing = await session.scalar(
        select(Notification.id)
        .where(Notification.user_id == user_id)
        .where(Notification.status == "pending")
        .where(Notification.quiz_id.is_(None))
        .where(Notification.content.contains(marker))
        .limit(1)
    )
    if existing is not None:
        return
    session.add(
        Notification(
            user_id=user_id,
            status="pending",
            target_datetime=datetime.now(UTC),
            content=content,
            quiz_id=None,
        )
    )


async def _complete_onboarding_notifications(
    session: AsyncSession,
    *,
    user_id: int,
    marker: str,
) -> None:
    await session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.status == "pending",
            Notification.quiz_id.is_(None),
            Notification.content.contains(marker),
        )
        .values(status="complete")
    )


async def _courses_by_id(session: AsyncSession, user_id: int) -> dict[int, Course]:
    rows = (
        (
            await session.execute(
                select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return {course.id: course for course in rows}


async def _missing_schedule_course_names(session: AsyncSession, user_id: int) -> list[str]:
    courses = (
        (
            await session.execute(
                select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
            )
        )
        .scalars()
        .all()
    )
    scheduled_course_ids = {
        course_id
        for course_id in (
            await session.execute(
                select(ScheduleEvent.course_id)
                .where(ScheduleEvent.user_id == user_id, ScheduleEvent.course_id.is_not(None))
            )
        )
        .scalars()
        .all()
        if course_id is not None
    }
    return [course.name for course in courses if course.id not in scheduled_course_ids]


async def _missing_deadline_course_names(session: AsyncSession, user_id: int) -> list[str]:
    courses = (
        (
            await session.execute(
                select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
            )
        )
        .scalars()
        .all()
    )
    deadline_course_ids = {
        course_id
        for course_id in (
            await session.execute(
                select(Deadline.course_id).where(Deadline.user_id == user_id)
            )
        )
        .scalars()
        .all()
    }
    return [course.name for course in courses if course.id not in deadline_course_ids]


async def _add_courses(
    session: AsyncSession,
    user_id: int,
    courses: list[str],
) -> list[Course]:
    if not courses:
        raise ValueError("no courses provided")

    existing_courses = (
        (
            await session.execute(
                select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
            )
        )
        .scalars()
        .all()
    )
    by_name = {course.name.casefold(): course for course in existing_courses}
    stored: list[Course] = []

    for raw_name in courses:
        name = str(raw_name).strip()
        if not name:
            raise ValueError("course names cannot be empty")
        existing = by_name.get(name.casefold())
        if existing is not None:
            stored.append(existing)
            continue
        course = Course(user_id=user_id, name=name)
        session.add(course)
        await session.flush()
        by_name[name.casefold()] = course
        stored.append(course)

    await _complete_onboarding_notifications(
        session, user_id=user_id, marker=_COURSE_NOTIFICATION_MARKER
    )
    await _upsert_followup_notification(
        session,
        user_id=user_id,
        marker=_SCHEDULE_NOTIFICATION_MARKER,
        content=(
            "Thanks. Please share the schedule for all of your courses in chat so I can "
            "save course-linked events."
        ),
    )
    await session.flush()
    return stored


async def _add_schedule_events(
    session: AsyncSession,
    user_id: int,
    events: list[dict[str, Any]],
) -> tuple[list[ScheduleEvent], list[str]]:
    log.debug(
        "chat._add_schedule_events start user_id=%d raw_event_count=%d",
        user_id,
        len(events),
    )
    if not events:
        raise ValueError("no schedule events provided")

    courses_by_id = await _courses_by_id(session, user_id)
    if not courses_by_id:
        raise ValueError("no saved courses exist yet for this user")

    normalized_events: list[ScheduleEvent] = []
    for raw_event in events:
        event_type = str(raw_event["type"]).strip().lower()
        if event_type not in _VALID_EVENT_TYPES:
            log.warning(
                "chat._add_schedule_events invalid_type user_id=%d type=%r raw_event=%r",
                user_id,
                event_type,
                raw_event,
            )
            raise ValueError(f"invalid schedule event type: {event_type}")

        start = _parse_iso_datetime(str(raw_event["start_datetime"]))
        end = _parse_iso_datetime(str(raw_event["end_datetime"]))
        if end <= start:
            log.warning(
                "chat._add_schedule_events invalid_range user_id=%d start=%s end=%s raw_event=%r",
                user_id,
                start.isoformat(),
                end.isoformat(),
                raw_event,
            )
            raise ValueError("schedule event end_datetime must be after start_datetime")

        name = str(raw_event["name"]).strip()
        course_id = int(raw_event["course_id"])
        if course_id not in courses_by_id:
            raise ValueError(f"unknown course_id: {course_id}")
        log.debug(
            "chat._add_schedule_events normalized_event user_id=%d course_id=%d type=%s name=%r start=%s end=%s",
            user_id,
            course_id,
            event_type,
            name,
            start.isoformat(),
            end.isoformat(),
        )
        normalized_events.append(
            ScheduleEvent(
                user_id=user_id,
                course_id=course_id,
                type=event_type,
                name=name,
                start_datetime=start,
                end_datetime=end,
            )
        )

    for event in normalized_events:
        session.add(event)

    await session.flush()
    missing_courses = await _missing_schedule_course_names(session, user_id)
    if not missing_courses:
        await _complete_onboarding_notifications(
            session, user_id=user_id, marker=_SCHEDULE_NOTIFICATION_MARKER
        )
        await _upsert_followup_notification(
            session,
            user_id=user_id,
            marker=_DEADLINE_NOTIFICATION_MARKER,
            content=(
                "Great. Please share any deadlines or exam dates for your courses so I can "
                "track them and remind you in time."
            ),
        )
    log.info(
        "chat._add_schedule_events committed user_id=%d added_count=%d event_names=%s",
        user_id,
        len(normalized_events),
        [event.name for event in normalized_events],
    )
    return normalized_events, missing_courses


async def _add_deadlines(
    session: AsyncSession,
    user_id: int,
    deadlines: list[dict[str, Any]],
) -> tuple[list[Deadline], list[str]]:
    log.debug(
        "chat._add_deadlines start user_id=%d raw_deadline_count=%d",
        user_id,
        len(deadlines),
    )
    if not deadlines:
        raise ValueError("no deadlines provided")

    courses_by_id = await _courses_by_id(session, user_id)
    if not courses_by_id:
        raise ValueError("no saved courses exist yet for this user")

    stored: list[Deadline] = []
    for raw_deadline in deadlines:
        course_id = int(raw_deadline["course_id"])
        if course_id not in courses_by_id:
            raise ValueError(f"unknown course_id: {course_id}")

        deadline_dt = _parse_iso_datetime(str(raw_deadline["datetime"]))
        name = str(raw_deadline["name"]).strip()
        if not name:
            raise ValueError("deadline name cannot be empty")

        deadline = Deadline(
            user_id=user_id,
            course_id=course_id,
            datetime=deadline_dt,
            name=name,
        )
        session.add(deadline)
        stored.append(deadline)

    await session.flush()
    missing_courses = await _missing_deadline_course_names(session, user_id)
    if not missing_courses:
        await _complete_onboarding_notifications(
            session, user_id=user_id, marker=_DEADLINE_NOTIFICATION_MARKER
        )
        await _upsert_followup_notification(
            session,
            user_id=user_id,
            marker=_PROFILE_NOTIFICATION_MARKER,
            content=(
                "Last onboarding question: what are your interests, and what are your goals "
                "for the future? I'll save that to your profile."
            ),
        )
    log.info(
        "chat._add_deadlines committed user_id=%d added_count=%d deadline_names=%s",
        user_id,
        len(stored),
        [deadline.name for deadline in stored],
    )
    return stored, missing_courses


async def _save_user_profile(
    session: AsyncSession,
    user_id: int,
    *,
    interests: str,
    future_goals: str,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"unknown user_id: {user_id}")

    cleaned_interests = interests.strip()
    cleaned_future_goals = future_goals.strip()
    if not cleaned_interests:
        raise ValueError("interests cannot be empty")
    if not cleaned_future_goals:
        raise ValueError("future goals cannot be empty")

    user.interests = cleaned_interests
    user.future_goals = cleaned_future_goals
    await _complete_onboarding_notifications(
        session, user_id=user_id, marker=_PROFILE_NOTIFICATION_MARKER
    )
    await session.flush()
    log.info(
        "chat._save_user_profile committed user_id=%d interests_len=%d future_goals_len=%d",
        user_id,
        len(cleaned_interests),
        len(cleaned_future_goals),
    )
    return user


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        normalized = parsed.replace(tzinfo=UTC)
        log.debug("chat._parse_iso_datetime naive_input=%r normalized=%s", value, normalized.isoformat())
        return normalized
    log.debug("chat._parse_iso_datetime aware_input=%r normalized=%s", value, parsed.isoformat())
    return parsed


async def _build_sqlite_context(session: AsyncSession, user_id: int) -> str:
    log.debug("chat._build_sqlite_context start user_id=%d", user_id)
    user = await session.get(User, user_id)
    if user is None:
        log.warning("chat._build_sqlite_context missing_user user_id=%d", user_id)
        raise ValueError(f"unknown user_id={user_id}")

    courses = (
        (
            await session.execute(
                select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
            )
        )
        .scalars()
        .all()
    )
    course_by_id = {course.id: course.name for course in courses}

    upcoming_events = (
        (
            await session.execute(
                select(ScheduleEvent)
                .where(ScheduleEvent.user_id == user_id)
                .order_by(ScheduleEvent.start_datetime.asc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    deadlines = (
        (
            await session.execute(
                select(Deadline)
                .where(Deadline.user_id == user_id)
                .order_by(Deadline.datetime.asc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    recent_quizzes = (
        (
            await session.execute(
                select(Quiz)
                .where(Quiz.user_id == user_id)
                .order_by(Quiz.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    pending_notifications = (
        (
            await session.execute(
                select(Notification)
                .where(Notification.user_id == user_id, Notification.status == "pending")
                .order_by(Notification.target_datetime.asc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    recent_results = (
        (
            await session.execute(
                select(QuizResult)
                .where(QuizResult.user_id == user_id)
                .order_by(QuizResult.quiz_taken_datetime.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    demo_state = await _demo_state_for_user(session, user_id)
    active_demo_quiz = (
        await session.get(Quiz, demo_state.quiz_id)
        if demo_state is not None and demo_state.quiz_id is not None
        else None
    )

    lines = [
        (
            f"User: username={user.username!r}, name={user.name!r}, email={user.email!r}, "
            f"interests={user.interests!r}, future_goals={user.future_goals!r}"
        ),
        "Courses:",
    ]
    if courses:
        lines.extend(
            f"- course_id={course.id}: {course.name}"
            for course in courses
        )
    else:
        lines.append("- none")

    lines.append("Upcoming schedule events:")
    if upcoming_events:
        lines.extend(
            (
                f"- {event.type}: {event.name} from {event.start_datetime.isoformat()} "
                f"to {event.end_datetime.isoformat()} "
                f"(course_id={event.course_id}, course={course_by_id.get(event.course_id)})"
            )
            for event in upcoming_events
        )
    else:
        lines.append("- none")

    lines.append("Courses without schedule events:")
    missing_schedule = await _missing_schedule_course_names(session, user_id)
    if missing_schedule:
        lines.extend(f"- {name}" for name in missing_schedule)
    else:
        lines.append("- none")

    lines.append("Upcoming deadlines:")
    if deadlines:
        lines.extend(
            (
                f"- {deadline.datetime.isoformat()}: {deadline.name} "
                f"(course_id={deadline.course_id}, course={course_by_id.get(deadline.course_id)})"
            )
            for deadline in deadlines
        )
    else:
        lines.append("- none")

    lines.append("Courses without deadlines:")
    missing_deadlines = await _missing_deadline_course_names(session, user_id)
    if missing_deadlines:
        lines.extend(f"- {name}" for name in missing_deadlines)
    else:
        lines.append("- none")

    lines.append("Recent quizzes:")
    if recent_quizzes:
        lines.extend(
            (
                f"- {quiz.title} on {quiz.topic} ({quiz.estimated_duration_minutes} min, "
                f"course_id={quiz.course_id}, course={course_by_id.get(quiz.course_id)})"
            )
            for quiz in recent_quizzes
        )
    else:
        lines.append("- none")

    lines.append("Pending notifications:")
    if pending_notifications:
        lines.extend(
            f"- {notification.target_datetime.isoformat()}: {notification.content}"
            for notification in pending_notifications
        )
    else:
        lines.append("- none")

    lines.append("Recent quiz results:")
    if recent_results:
        lines.extend(
            f"- quiz_id={result.quiz_id}: correct={result.correct_answers}, false={result.false_answers}, taken={result.quiz_taken_datetime.isoformat()}"
            for result in recent_results
        )
    else:
        lines.append("- none")

    lines.append("Demo conversation state:")
    if demo_state is None:
        lines.append("- none")
    else:
        lines.append(
            f"- status={demo_state.status}, course={demo_state.course_name}, "
            f"coverage_percent={demo_state.coverage_percent}, quiz_id={demo_state.quiz_id}, "
            f"average_score_percent={demo_state.average_score_percent}"
        )
    lines.append("Active demo quiz:")
    if active_demo_quiz is None:
        lines.append("- none")
    else:
        lines.append(
            f"- quiz_id={active_demo_quiz.id}, title={active_demo_quiz.title}, "
            f"topic={active_demo_quiz.topic}, question_count={len(active_demo_quiz.questions)}"
        )
        for index, question in enumerate(active_demo_quiz.questions, start=1):
            lines.append(
                f"  question_{index}: {str(question.get('question', '')).strip()}"
            )
            lines.append(
                f"  answer_{index}: {str(question.get('answer', '')).strip()}"
            )

    context = "\n".join(lines)
    log.debug(
        "chat._build_sqlite_context done user_id=%d events=%d quizzes=%d pending_notifications=%d results=%d context_len=%d",
        user_id,
        len(upcoming_events),
        len(recent_quizzes),
        len(pending_notifications),
        len(recent_results),
        len(context),
    )
    return context


async def _build_cognee_context(query: str) -> str:
    log.debug("chat._build_cognee_context start query_len=%d preview=%r", len(query), _preview(query))
    combined = await cognee_service.query_combined_context(
        diary_query=query,
        materials_query=query,
    )
    log.debug(
        "chat._build_cognee_context done total_len=%d",
        len(combined),
    )
    return combined
