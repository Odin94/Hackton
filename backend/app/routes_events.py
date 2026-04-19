"""Event discovery routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from agent.database import AsyncSessionLocal
from agent.models import DiscoveredEvent
from app.auth import get_user_id
from app.event_discovery import discover_events_for_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


def _require_user_id(authorization: str | None) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization token")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = get_user_id(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user_id


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class EventItem(BaseModel):
    id: int
    title: str
    description: str
    url: str | None
    location: str | None
    event_date: str | None
    signup_deadline: str | None
    category: str
    score: int
    score_reasoning: str | None
    notified: bool


class DiscoverResp(BaseModel):
    total_events_found: int
    top_events: list[dict]


class EventListResp(BaseModel):
    events: list[EventItem]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/discover", response_model=DiscoverResp)
async def discover_events(
    authorization: str | None = Header(default=None),
) -> DiscoverResp:
    """Trigger LLM-powered event discovery for the authenticated user.

    Searches the web, scores results, persists them, and queues notifications
    for the top-3 events. Returns the top events immediately.
    """
    user_id = _require_user_id(authorization)
    try:
        result = await discover_events_for_user(user_id)
    except Exception as exc:
        log.exception("Event discovery failed for user_id=%d", user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return DiscoverResp(**result)


@router.get("/", response_model=EventListResp)
async def list_discovered_events(
    authorization: str | None = Header(default=None),
) -> EventListResp:
    """Return all previously discovered events for the authenticated user."""
    user_id = _require_user_id(authorization)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(DiscoveredEvent)
                .where(DiscoveredEvent.user_id == user_id)
                .order_by(DiscoveredEvent.score.desc())
            )
        ).scalars().all()

    return EventListResp(
        events=[
            EventItem(
                id=r.id,
                title=r.title,
                description=r.description,
                url=r.url,
                location=r.location,
                event_date=r.event_date_str,
                signup_deadline=r.signup_deadline_str,
                category=r.category,
                score=r.score,
                score_reasoning=r.score_reasoning,
                notified=bool(r.notified),
            )
            for r in rows
        ]
    )
