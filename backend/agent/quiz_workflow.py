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
from app.connection_manager import manager as ws_manager

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
    log.debug("generate_quizzes_impl: loading events for user_id=%d", user_id)
    events = (
        await session.execute(
            select(ScheduleEvent).where(ScheduleEvent.user_id == user_id)
        )
    ).scalars().all()

    if not events:
        log.info("No schedule events found for user_id=%d", user_id)
        return []

    log.debug("generate_quizzes_impl: found %d event(s) for user_id=%d", len(events), user_id)
    notification_ids: list[int] = []

    for event in events:
        log.debug("Processing event='%s' user_id=%d end=%s", event.name, user_id, event.end_datetime)
        # ------------------------------------------------------------------
        # Cognee query — gracefully degrade if the index is empty
        # ------------------------------------------------------------------
        material_context: str = ""
        try:
            material_context = await query_materials(event.name)
            log.debug("Cognee material fetched event='%s' context_len=%d", event.name, len(material_context))
        except NoDataError:
            log.info("No cognee material for event '%s' — using topic-only prompt", event.name)
        except Exception:
            log.warning("Cognee query failed for event '%s'", event.name, exc_info=True)

        # ------------------------------------------------------------------
        # Quiz generation
        # ------------------------------------------------------------------
        log.debug("Generating quiz for event='%s' has_material=%s", event.name, bool(material_context))
        try:
            quiz_items = await cognee_generate_quiz(topic=event.name)
            log.debug("Quiz generated event='%s' items=%d", event.name, len(quiz_items))
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
        duration_mins = max(1, len(quiz_items) * _MINUTES_PER_QUESTION)
        log.debug("Persisting quiz event='%s' items=%d estimated_duration_minutes=%d", event.name, len(quiz_items), duration_mins)
        quiz = Quiz(
            user_id=user_id,
            title=f"Quiz: {event.name}",
            topic=event.name,
            estimated_duration_minutes=duration_mins,
            questions=[item.model_dump() for item in quiz_items],
        )
        quiz.schedule_events.append(event)
        session.add(quiz)
        await session.flush()  # populates quiz.id before we reference it below
        log.debug("Quiz flushed quiz_id=%d event='%s'", quiz.id, event.name)

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
        log.debug("Notification flushed notification_id=%d quiz_id=%d event='%s'", notif.id, quiz.id, event.name)
        log.info(
            "Created quiz_id=%d notification_id=%d for event '%s'",
            quiz.id, notif.id, event.name,
        )

    log.debug("Committing %d quiz/notification pairs for user_id=%d", len(notification_ids), user_id)
    await session.commit()
    log.debug("Commit complete; notification_ids=%s", notification_ids)
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
    """Send due pending notifications over WebSocket and mark them complete.

    Notifications for offline users are left pending and retried on the next
    scheduler tick.  Returns the number of notifications actually dispatched.
    """
    now = datetime.now(UTC)
    log.debug("dispatch_due_notifications: querying due notifications at now=%s", now.isoformat())

    due = (
        await session.execute(
            select(Notification)
            .where(Notification.status == "pending")
            .where(Notification.target_datetime <= now)
        )
    ).scalars().all()

    log.debug("dispatch_due_notifications: found %d due notification(s)", len(due))
    dispatched = 0
    for notif in due:
        # Skip if the user has no active WebSocket connection — retry next tick.
        if not ws_manager.is_connected(notif.user_id):
            log.debug(
                "Notification id=%d deferred — user_id=%d not connected",
                notif.id, notif.user_id,
            )
            continue

        quiz_payload: dict | None = None
        if notif.quiz_id is not None:
            quiz = await session.get(Quiz, notif.quiz_id)
            if quiz is not None:
                quiz_payload = {
                    "id": quiz.id,
                    "title": quiz.title,
                    "topic": quiz.topic,
                    "estimated_duration_minutes": quiz.estimated_duration_minutes,
                    "questions": quiz.questions,
                }

        sent = await ws_manager.send(
            notif.user_id,
            {
                "type": "notification",
                "notification_id": notif.id,
                "content": notif.content,
                "quiz": quiz_payload,
            },
        )

        if not sent:
            # Race: connection dropped between is_connected() and send()
            log.warning(
                "WS send failed (race) for user_id=%d notification_id=%d — will retry",
                notif.user_id, notif.id,
            )
            continue

        log.info(
            "WS notification sent → user_id=%d notification_id=%d",
            notif.user_id, notif.id,
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
