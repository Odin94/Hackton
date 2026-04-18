"""Minimal chat service backed by SQLite history plus cognee retrieval."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import litellm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.database import AsyncSessionLocal
from agent.models import ChatMessage, Notification, Quiz, QuizResult, ScheduleEvent, User
from app import cognee_service
from app.cognee_service import CogneeServiceError, NoDataError
from app.config import settings

log = logging.getLogger(__name__)

_PROMPT_HISTORY_MESSAGES = 12


async def list_chat_messages(user_id: int) -> list[ChatMessage]:
    async with AsyncSessionLocal() as session:
        return await _list_chat_messages(session, user_id)


async def append_chat_message(
    session: AsyncSession,
    *,
    user_id: int,
    author: str,
    content: str,
    timestamp: datetime | None = None,
) -> ChatMessage:
    next_sequence = await _next_sequence_number(session, user_id)
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

    async with AsyncSessionLocal() as session:
        user_message = await append_chat_message(
            session, user_id=user_id, author="user", content=cleaned
        )
        response_text = await _generate_chat_response(session, user_id, cleaned)
        system_message = await append_chat_message(
            session, user_id=user_id, author="system", content=response_text
        )
        await session.commit()
        await session.refresh(user_message)
        await session.refresh(system_message)
        return user_message, system_message


async def _list_chat_messages(session: AsyncSession, user_id: int) -> list[ChatMessage]:
    rows = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.sequence_number.asc())
    )
    return list(rows.scalars().all())


async def _next_sequence_number(session: AsyncSession, user_id: int) -> int:
    current = await session.scalar(
        select(func.max(ChatMessage.sequence_number)).where(ChatMessage.user_id == user_id)
    )
    return (current or 0) + 1


async def _generate_chat_response(
    session: AsyncSession,
    user_id: int,
    latest_user_message: str,
) -> str:
    history, sqlite_context, cognee_context = await asyncio.gather(
        _list_chat_messages(session, user_id),
        _build_sqlite_context(session, user_id),
        _build_cognee_context(latest_user_message),
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
        "Treat 'system' chat messages as prior assistant replies."
    )
    user_prompt = (
        f"User ID: {user_id}\n\n"
        f"Latest user message:\n{latest_user_message}\n\n"
        f"Recent chat history:\n{history_lines}\n\n"
        f"SQLite application context:\n{sqlite_context}\n\n"
        f"Cognee context:\n{cognee_context}\n"
    )

    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            ),
            timeout=settings.llm_call_timeout_seconds,
        )
    except TimeoutError as e:
        raise cognee_service.LLMTimeoutError(
            f"LLM call exceeded {settings.llm_call_timeout_seconds}s timeout"
        ) from e
    except Exception as e:
        raise cognee_service._wrap(e) from e

    content = getattr(response.choices[0].message, "content", "") or ""
    cleaned = content.strip()
    if not cleaned:
        raise cognee_service.MalformedLLMResponseError("chat generation returned empty content")
    return cleaned


async def _build_sqlite_context(session: AsyncSession, user_id: int) -> str:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"unknown user_id={user_id}")

    upcoming_events = (
        (
            await session.execute(
                select(ScheduleEvent)
                .where(ScheduleEvent.user_id == user_id)
                .order_by(ScheduleEvent.start_datetime.asc())
                .limit(5)
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

    lines = [
        f"User: username={user.username!r}, name={user.name!r}, email={user.email!r}",
        "Upcoming schedule events:",
    ]
    if upcoming_events:
        lines.extend(
            f"- {event.type}: {event.name} from {event.start_datetime.isoformat()} to {event.end_datetime.isoformat()}"
            for event in upcoming_events
        )
    else:
        lines.append("- none")

    lines.append("Recent quizzes:")
    if recent_quizzes:
        lines.extend(
            f"- {quiz.title} on {quiz.topic} ({quiz.estimated_duration_minutes} min)"
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

    return "\n".join(lines)


async def _build_cognee_context(query: str) -> str:
    diary_task = asyncio.create_task(_safe_query(cognee_service.query_diary, query))
    materials_task = asyncio.create_task(_safe_query(cognee_service.query_materials, query))
    diary_answer, materials_answer = await asyncio.gather(diary_task, materials_task)
    return (
        "Diary retrieval:\n"
        f"{diary_answer}\n\n"
        "Materials retrieval:\n"
        f"{materials_answer}"
    )


async def _safe_query(fn, query: str) -> str:
    try:
        answer = await fn(query)
    except NoDataError:
        return "No relevant context found."
    except CogneeServiceError as e:
        log.warning("chat retrieval fallback query=%r error=%s", query, e)
        return f"Retrieval unavailable: {e}"
    return answer.strip() or "No relevant context found."
