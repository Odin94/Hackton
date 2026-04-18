"""Background scheduler — two concurrent asyncio loops.

LLM check-in loop (every 1 h)
    Reviews diary + app state for each user and decides whether to persist an
    insight and/or queue proactive notifications.

Notification dispatch loop (every 60 s)
    Queries the DB for pending Notifications whose target_datetime has passed
    and delivers them (WebSocket send is currently mocked — see quiz_workflow.py).

Both loops are started from ``start_scheduler()``, which returns a pair of Tasks
so the caller can cancel them cleanly on shutdown.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.cognee_service import query_combined_context

from .database import AsyncSessionLocal
from .db import list_user_ids, read_recent
from .harness import _quiz_llm_call
from .models import Course, Deadline, Notification, Quiz, QuizResult, ScheduleEvent, User
from .quiz_workflow import dispatch_due_notifications

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

LLM_CHECKIN_INTERVAL = 3600  # 1 hour
NOTIFICATION_DISPATCH_INTERVAL = 60  # 1 minute

# ---------------------------------------------------------------------------
# LLM check-in loop
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are StudyBot's autonomous study coach. You run in the background and help a
student make better study decisions, stay ahead of deadlines, and reinforce
healthy habits discovered from their diary and available course materials.

You have access to two tools:
- write_to_db: persist durable insights, patterns, or concerns for later use.
- schedule_notification: queue a proactive chat notification for the user.

Rules:
- Use ONLY the supplied Cognee retrieval, SQLite app state, and recent agent-log entries.
- Prefer concrete, timely interventions over generic encouragement.
- Good notifications are short and actionable: reminder before an event, recovery
  nudge after slipping, habit advice grounded in diary evidence, or a prompt to
  do a quiz/review at the right time.
- Avoid duplicates if a similar pending notification already exists.
- Be selective: zero notifications is fine when there is nothing useful to do.
"""

def _build_llm_user_prompt(
    *,
    user_id: int,
    sqlite_context: str,
    cognee_context: str,
    recent: list[dict],
) -> str:
    if recent:
        rows = "\n".join(
            f"  [{r['created_at']}] ({r['entry_type']}) {r['content'][:120]}"
            for r in recent
        )
        context = f"Most recent agent-log entries (newest first):\n{rows}"
    else:
        context = "The agent log is currently empty — nothing has been recorded yet."

    return (
        f"It's your scheduled hourly check-in for user {user_id}.\n\n"
        f"Current UTC time: {datetime.now(UTC).isoformat()}\n\n"
        f"SQLite application context:\n{sqlite_context}\n\n"
        f"Cognee context:\n{cognee_context}\n\n"
        f"Recent agent-log entries:\n{context}\n\n"
        "Decide whether there is anything worth doing right now:\n"
        "- store a durable study insight,\n"
        "- queue a proactive notification, or\n"
        "- do nothing.\n"
        "When scheduling a notification, make it specific and supportive."
    )


async def _build_scheduler_sqlite_context(user_id: int) -> str:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError(f"unknown user_id={user_id}")

        now = datetime.now(UTC)
        upcoming_cutoff = now + timedelta(days=7)
        recent_cutoff = now - timedelta(days=14)

        courses = (
            (
                await session.execute(
                    select(Course).where(Course.user_id == user_id).order_by(Course.id.asc())
                )
            )
            .scalars()
            .all()
        )
        upcoming_events = (
            (
                await session.execute(
                    select(ScheduleEvent)
                    .where(ScheduleEvent.user_id == user_id)
                    .where(ScheduleEvent.start_datetime >= now)
                    .where(ScheduleEvent.start_datetime <= upcoming_cutoff)
                    .order_by(ScheduleEvent.start_datetime.asc())
                )
            )
            .scalars()
            .all()
        )
        upcoming_deadlines = (
            (
                await session.execute(
                    select(Deadline)
                    .where(Deadline.user_id == user_id)
                    .where(Deadline.datetime >= now)
                    .where(Deadline.datetime <= upcoming_cutoff)
                    .order_by(Deadline.datetime.asc())
                )
            )
            .scalars()
            .all()
        )
        pending_notifications = (
            (
                await session.execute(
                    select(Notification)
                    .where(Notification.user_id == user_id)
                    .where(Notification.status == "pending")
                    .order_by(Notification.target_datetime.asc())
                    .limit(8)
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
                    .where(QuizResult.quiz_taken_datetime >= recent_cutoff)
                    .order_by(QuizResult.quiz_taken_datetime.desc())
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

    lines = [
        f"User: username={user.username!r}, name={user.name!r}, email={user.email!r}",
        "Courses:",
    ]
    if courses:
        lines.extend(f"- course_id={course.id}: {course.name}" for course in courses)
    else:
        lines.append("- none")

    lines.append("Upcoming deadlines in the next 7 days:")
    if upcoming_deadlines:
        lines.extend(
            f"- {deadline.datetime.isoformat()}: {deadline.name} (course_id={deadline.course_id})"
            for deadline in upcoming_deadlines
        )
    else:
        lines.append("- none")

    lines.extend([
        "Upcoming schedule events in the next 7 days:",
    ])
    if upcoming_events:
        lines.extend(
            f"- {event.type}: {event.name} from {event.start_datetime.isoformat()} to {event.end_datetime.isoformat()}"
            for event in upcoming_events
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

    lines.append("Recent quizzes:")
    if recent_quizzes:
        lines.extend(
            f"- {quiz.title} on {quiz.topic} ({quiz.estimated_duration_minutes} min)"
            for quiz in recent_quizzes
        )
    else:
        lines.append("- none")

    lines.append("Recent quiz results from the last 14 days:")
    if recent_results:
        lines.extend(
            f"- quiz_id={result.quiz_id}: correct={result.correct_answers}, false={result.false_answers}, taken={result.quiz_taken_datetime.isoformat()}"
            for result in recent_results
        )
    else:
        lines.append("- none")

    return "\n".join(lines)


async def _build_scheduler_cognee_context() -> str:
    diary_query = (
        "Summarize the student's study habits, energy patterns, setbacks, healthy routines, "
        "and the most helpful next nudges or reminders a study coach should send soon."
    )
    materials_query = (
        "Summarize the important topics, concepts, and study areas present in the available "
        "course materials that would help a study coach suggest timely review or quiz practice."
    )
    return await query_combined_context(
        diary_query=diary_query,
        materials_query=materials_query,
    )


async def _llm_checkin_loop() -> None:
    log.info("LLM check-in loop started (interval=%ds)", LLM_CHECKIN_INTERVAL)
    while True:
        await asyncio.sleep(LLM_CHECKIN_INTERVAL)
        log.info("Scheduler: LLM check-in starting")
        try:
            recent = await read_recent(limit=10)
            user_ids = await list_user_ids()
            if not user_ids:
                log.info("Scheduler: no users found for proactive check-in")
                continue

            cognee_context = await _build_scheduler_cognee_context()
            log.debug(
                "Scheduler: LLM check-in fetched %d recent log entries for %d user(s)",
                len(recent),
                len(user_ids),
            )
            for user_id in user_ids:
                try:
                    sqlite_context = await _build_scheduler_sqlite_context(user_id)
                    messages = await _quiz_llm_call(
                        _LLM_SYSTEM_PROMPT,
                        _build_llm_user_prompt(
                            user_id=user_id,
                            sqlite_context=sqlite_context,
                            cognee_context=cognee_context,
                            recent=recent,
                        ),
                    )
                    log.debug(
                        "Scheduler: LLM check-in conversation turns=%d user_id=%d",
                        len(messages),
                        user_id,
                    )
                    final = next(
                        (m.get("content") for m in reversed(messages) if m.get("role") == "assistant"),
                        "(no reply)",
                    )
                    log.info(
                        "Scheduler: proactive check-in done user_id=%d — %s",
                        user_id,
                        (final or "")[:200],
                    )
                except Exception:
                    log.exception("Scheduler: proactive check-in failed for user_id=%d", user_id)
        except Exception:
            log.exception("Scheduler: LLM check-in failed")


# ---------------------------------------------------------------------------
# Notification dispatch loop
# ---------------------------------------------------------------------------


async def _notification_dispatch_loop() -> None:
    log.info("Notification dispatch loop started (interval=%ds)", NOTIFICATION_DISPATCH_INTERVAL)
    while True:
        await asyncio.sleep(NOTIFICATION_DISPATCH_INTERVAL)
        log.debug("Scheduler: notification dispatch tick")
        try:
            n = await dispatch_due_notifications()
            if n:
                log.info("Scheduler: dispatched %d notification(s)", n)
            else:
                log.debug("Scheduler: no due notifications this tick")
        except Exception:
            log.exception("Scheduler: notification dispatch failed")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def start_scheduler() -> tuple[asyncio.Task, asyncio.Task]:
    """Spawn both background loops.

    Must be called from inside a running event loop (e.g. a FastAPI lifespan).
    Returns ``(llm_task, dispatch_task)`` so callers can cancel on shutdown::

        llm_task, dispatch_task = start_scheduler()
        ...
        llm_task.cancel()
        dispatch_task.cancel()
    """
    llm_task = asyncio.create_task(_llm_checkin_loop(), name="agent-llm-checkin")
    dispatch_task = asyncio.create_task(
        _notification_dispatch_loop(), name="agent-notification-dispatch"
    )
    return llm_task, dispatch_task
