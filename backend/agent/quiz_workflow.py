"""Quiz generation workflow.

``generate_quizzes_for_user_events`` is the main entry-point:

1. Load the user's ScheduleEvents from the DB.
2. For each event, query Cognee for related study material.
3. Generate a Quiz from that material and persist it (linked to the event).
4. Create a pending Notification due at the event's end_datetime.

Returns the list of Notification IDs that were created so callers can track them.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cognee_service import NoDataError, generate_quiz as cognee_generate_quiz
from app.cognee_service import query_materials

from .database import AsyncSessionLocal
from .models import Notification, Quiz, ScheduleEvent

log = logging.getLogger(__name__)

# Estimate 2 minutes per quiz question when we have no better signal.
_MINUTES_PER_QUESTION = 2


# ---------------------------------------------------------------------------
# Internal implementation — accepts an injected session so tests can pass
# their own in-memory session without patching module globals.
# ---------------------------------------------------------------------------


async def _generate_quizzes_impl(user_id: int, session: AsyncSession) -> list[int]:
    events = (
        await session.execute(
            select(ScheduleEvent).where(ScheduleEvent.user_id == user_id)
        )
    ).scalars().all()

    if not events:
        log.info("No schedule events found for user_id=%d", user_id)
        return []

    notification_ids: list[int] = []

    for event in events:
        # ------------------------------------------------------------------
        # Cognee query — gracefully degrade if the index is empty
        # ------------------------------------------------------------------
        material_context: str = ""
        try:
            material_context = await query_materials(event.name)
        except NoDataError:
            log.info("No cognee material for event '%s' — using topic-only prompt", event.name)
        except Exception:
            log.warning("Cognee query failed for event '%s'", event.name, exc_info=True)

        # ------------------------------------------------------------------
        # Quiz generation
        # ------------------------------------------------------------------
        try:
            quiz_items = await cognee_generate_quiz(topic=event.name)
        except NoDataError:
            log.warning(
                "Skipping quiz for event '%s': no cognee data available", event.name
            )
            continue
        except Exception:
            log.warning("Quiz generation failed for event '%s'", event.name, exc_info=True)
            continue

        # ------------------------------------------------------------------
        # Persist Quiz
        # ------------------------------------------------------------------
        quiz = Quiz(
            user_id=user_id,
            title=f"Quiz: {event.name}",
            topic=event.name,
            estimated_duration_minutes=max(1, len(quiz_items) * _MINUTES_PER_QUESTION),
            questions=[item.model_dump() for item in quiz_items],
        )
        quiz.schedule_events.append(event)
        session.add(quiz)
        await session.flush()  # populates quiz.id before we reference it below

        # ------------------------------------------------------------------
        # Create Notification — due when the event ends
        # ------------------------------------------------------------------
        notif = Notification(
            user_id=user_id,
            status="pending",
            target_datetime=event.end_datetime,
            content=(
                f"Time to test your knowledge on '{event.name}'! "
                "Your personalised quiz is ready."
            ),
            quiz_id=quiz.id,
        )
        session.add(notif)
        await session.flush()
        notification_ids.append(notif.id)
        log.info(
            "Created quiz_id=%d notification_id=%d for event '%s'",
            quiz.id, notif.id, event.name,
        )

    await session.commit()
    return notification_ids


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


async def generate_quizzes_for_user_events(user_id: int) -> list[int]:
    """Generate quizzes for every ScheduleEvent belonging to *user_id*.

    Returns the IDs of all Notifications created (one per generated quiz).
    """
    async with AsyncSessionLocal() as session:
        return await _generate_quizzes_impl(user_id, session)


# ---------------------------------------------------------------------------
# Notification dispatch — called by the scheduler
# ---------------------------------------------------------------------------


async def _dispatch_due_notifications_impl(session: AsyncSession) -> int:
    """Mark due pending notifications as complete and (mock) send via WebSocket.

    Returns the number of notifications dispatched.
    """
    now = datetime.now(UTC)

    due = (
        await session.execute(
            select(Notification)
            .where(Notification.status == "pending")
            .where(Notification.target_datetime <= now)
        )
    ).scalars().all()

    dispatched = 0
    for notif in due:
        # ------------------------------------------------------------------
        # TODO: Check for an active WebSocket connection for notif.user_id.
        #
        #   ws = websocket_manager.get_connection(notif.user_id)
        #   if ws is None:
        #       continue   # user is offline — retry next tick
        #
        # ------------------------------------------------------------------

        quiz_payload: dict | None = None
        if notif.quiz_id is not None:
            # Eagerly load the quiz within the same session
            quiz = await session.get(Quiz, notif.quiz_id)
            if quiz is not None:
                quiz_payload = {
                    "id": quiz.id,
                    "title": quiz.title,
                    "topic": quiz.topic,
                    "estimated_duration_minutes": quiz.estimated_duration_minutes,
                    "questions": quiz.questions,
                }

        # ------------------------------------------------------------------
        # TODO: Replace the log line below with the actual WebSocket send:
        #
        #   await ws.send_json({"content": notif.content, "quiz": quiz_payload})
        #
        # ------------------------------------------------------------------
        log.info(
            "MOCK WS → user_id=%d notification_id=%d content=%r quiz_id=%s",
            notif.user_id,
            notif.id,
            notif.content,
            notif.quiz_id,
        )

        notif.status = "complete"
        dispatched += 1

    if dispatched:
        await session.commit()
    log.debug("Notification dispatch: %d sent", dispatched)
    return dispatched


async def dispatch_due_notifications() -> int:
    """Dispatch all pending notifications whose target_datetime has passed.

    Returns the number of notifications that were dispatched.
    """
    async with AsyncSessionLocal() as session:
        return await _dispatch_due_notifications_impl(session)
