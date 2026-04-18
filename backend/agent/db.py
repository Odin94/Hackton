"""High-level DB helpers used by the agent harness.

Public API
----------
init_db()           — create all tables (called from FastAPI lifespan)
write_entry()       — append a row to agent_log; returns new row-id
read_recent()       — return the N most-recent agent_log rows as plain dicts
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import AsyncSessionLocal, create_all_tables
from .models import AgentLog

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


# ---------------------------------------------------------------------------
# Generic session helper — useful for tests and workflow functions that need
# to share a session across multiple operations.
# ---------------------------------------------------------------------------

async def get_session() -> AsyncSession:  # pragma: no cover
    """Return a bare AsyncSession.  Caller is responsible for commit/close."""
    return AsyncSessionLocal()
