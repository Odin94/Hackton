"""High-level DB helpers used by the agent harness.

Public API
----------
init_db()           — create all tables (called from FastAPI lifespan)
write_entry()       — append a row to agent_log; returns new row-id
read_recent()       — return the N most-recent agent_log rows as plain dicts
list_user_ids()     — return all known user ids
create_notification() — queue a notification, deduping exact pending matches
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import AsyncSessionLocal, create_all_tables
from .models import AgentLog, Notification, User

log = logging.getLogger(__name__)


async def init_db() -> None:
    """Bootstrap: apply additive schema changes and create missing tables."""
    await create_all_tables()


async def write_entry(entry_type: str, content: str) -> int:
    """Insert one agent_log row and return its new id."""
    async with AsyncSessionLocal() as session:
        entry = AgentLog(entry_type=entry_type, content=content)
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        log.debug("AgentLog id=%d type=%s", entry.id, entry_type)
        return entry.id


async def read_recent(limit: int = 20) -> list[dict]:
    """Return the *limit* most-recent agent_log rows, newest first."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AgentLog).order_by(AgentLog.id.desc()).limit(limit)
            )
        ).scalars().all()
        return [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "entry_type": r.entry_type,
                "content": r.content,
            }
            for r in rows
        ]


async def list_user_ids() -> list[int]:
    """Return all known user ids in ascending order."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(User.id).order_by(User.id.asc()))
        return list(rows.scalars().all())


async def create_notification(
    user_id: int,
    content: str,
    target_datetime: datetime,
    *,
    quiz_id: int | None = None,
) -> int:
    """Queue a notification unless an identical pending one already exists."""
    cleaned = content.strip()
    if not cleaned:
        raise ValueError("notification content cannot be empty")

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(Notification.id)
            .where(Notification.user_id == user_id)
            .where(Notification.status == "pending")
            .where(Notification.target_datetime == target_datetime)
            .where(Notification.content == cleaned)
            .where(Notification.quiz_id.is_(quiz_id) if quiz_id is None else Notification.quiz_id == quiz_id)
            .limit(1)
        )
        if existing is not None:
            log.debug(
                "Reusing existing pending notification id=%d user_id=%d target=%s",
                existing,
                user_id,
                target_datetime.isoformat(),
            )
            return int(existing)

        notification = Notification(
            user_id=user_id,
            status="pending",
            target_datetime=target_datetime,
            content=cleaned,
            quiz_id=quiz_id,
        )
        session.add(notification)
        await session.commit()
        await session.refresh(notification)
        log.debug(
            "Notification id=%d queued for user_id=%d target=%s",
            notification.id,
            user_id,
            target_datetime.isoformat(),
        )
        return notification.id


# ---------------------------------------------------------------------------
# Generic session helper — useful for tests and workflow functions that need
# to share a session across multiple operations.
# ---------------------------------------------------------------------------

async def get_session() -> AsyncSession:  # pragma: no cover
    """Return a bare AsyncSession.  Caller is responsible for commit/close."""
    return AsyncSessionLocal()
