"""Event discovery via LLM over pre-scraped pages.

Pipeline
--------
1. Load fresh EventScrapingResult rows from the DB (scraped by app/scraper.py).
2. Build a rich user profile:
   - Courses, onboarding chat history, recent agent log observations
   - Cognee context (diary + uploaded materials) for deeper preference signals
3. Send scraped page texts + user profile to the LLM in one call.
4. LLM returns structured events: title, date, category, score, reasoning.
5. Persist results to DiscoveredEvent; create notifications for top-3.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import litellm
from sqlalchemy import select

from agent.database import AsyncSessionLocal
from agent.db import create_notification
from agent.models import AgentLog, ChatMessage, Course, DiscoveredEvent, EventScrapingResult, User
from app.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User profile builder
# ---------------------------------------------------------------------------


async def _build_user_profile(user_id: int) -> dict:
    """Collect all signals we have about a user: courses, full chat, agent obs."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError(f"user {user_id} not found")

        courses = (
            await session.execute(select(Course).where(Course.user_id == user_id))
        ).scalars().all()

        # Full chat history — onboarding preferences live here
        all_msgs = (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.user_id == user_id)
                .order_by(ChatMessage.sequence_number.asc())
            )
        ).scalars().all()

        agent_logs = (
            await session.execute(
                select(AgentLog).order_by(AgentLog.id.desc()).limit(20)
            )
        ).scalars().all()

    chat_text = "\n".join(f"[{m.author}] {m.content}" for m in all_msgs)
    log_text = "\n".join(f"- {a.content}" for a in agent_logs)

    return {
        "name": user.name or user.username,
        "courses": [c.name for c in courses],
        "chat_history": chat_text[:6000],   # includes onboarding goals + preferences
        "agent_observations": log_text[:1500],
    }


async def _cognee_user_context(user_id: int) -> str:  # noqa: ARG001
    """Try to pull diary + materials context from cognee; return empty string on failure."""
    try:
        from app.cognee_service import query_combined_context

        return await query_combined_context(
            diary_query="user interests goals hobbies preferences",
            materials_query="subjects topics user studies",
        )
    except Exception as exc:
        log.warning("Cognee context unavailable: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Load scraped pages
# ---------------------------------------------------------------------------


async def _load_scraped_pages() -> list[dict]:
    """Return all non-expired EventScrapingResult rows."""
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(EventScrapingResult)
                .where(EventScrapingResult.expires_at > now)
                .order_by(EventScrapingResult.id.asc())
            )
        ).scalars().all()
    return [{"url": r.url, "title": r.title or "", "text": r.text_content} for r in rows]


# ---------------------------------------------------------------------------
# LLM recommendation call
# ---------------------------------------------------------------------------


async def _llm_json(messages: list[dict]) -> dict:
    resp = await litellm.acompletion(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


async def _recommend_events(pages: list[dict], profile: dict, cognee_ctx: str) -> list[dict]:
    """Ask the LLM to identify events and score them for this user."""
    now = datetime.now(UTC)
    today_str = now.strftime("%Y-%m-%d")

    # Format scraped pages — cap per-page text to keep total prompt manageable
    pages_text = ""
    for i, p in enumerate(pages[:50], 1):
        pages_text += f"\n\n--- Page {i}: {p['url']} ---\n"
        if p["title"]:
            pages_text += f"Title: {p['title']}\n"
        pages_text += p["text"][:3000]

    courses_str = ", ".join(profile["courses"]) or "unknown"
    cognee_section = f"\nAdditional context from user's diary/materials:\n{cognee_ctx[:2000]}" if cognee_ctx else ""

    system = """\
You are an event recommendation engine for a student.

Your task:
1. Read each scraped page and determine whether it describes a real event (yes/no).
2. For pages that are events, extract structured information.
3. Score each event 0–100 for this specific student based on their profile.
   - Consider: their courses/field of study, stated interests and goals from their chat history,
     personal preferences mentioned during onboarding, and agent observations.
4. Always return at least the top 3 events even if the match score is low.
5. Filter out events that are already over (date in the past relative to today).
6. Page text may be in German — handle it natively.

Return JSON only:
{
  "events": [
    {
      "title": "...",
      "description": "2–3 sentence summary in English",
      "url": "...",
      "event_date_str": "YYYY-MM-DD or null",
      "location": "city / online / null",
      "category": "career | fun | networking | other",
      "score": 0-100,
      "score_reasoning": "one sentence why this student would or wouldn't like it"
    }
  ]
}"""

    user_msg = f"""\
Today: {today_str}

Student profile
---------------
Name: {profile['name']}
Courses: {courses_str}

Full chat history (includes onboarding goals and stated preferences):
{profile['chat_history']}

Agent observations about this student:
{profile['agent_observations']}
{cognee_section}

Scraped event pages
-------------------
{pages_text}"""

    result = await _llm_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ])
    events = result.get("events", [])
    log.info("LLM extracted %d events from %d scraped pages", len(events), len(pages))
    return events


# ---------------------------------------------------------------------------
# Persist + notify
# ---------------------------------------------------------------------------


async def _persist_and_notify(user_id: int, events: list[dict], now: datetime) -> list[DiscoveredEvent]:
    from sqlalchemy import delete as sa_delete

    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_delete(DiscoveredEvent).where(DiscoveredEvent.user_id == user_id)
        )
        rows: list[DiscoveredEvent] = []
        for e in events:
            row = DiscoveredEvent(
                user_id=user_id,
                title=str(e.get("title", "Untitled"))[:512],
                description=str(e.get("description", ""))[:2000],
                url=str(e.get("url", ""))[:1024] or None,
                location=str(e.get("location") or "")[:256] or None,
                event_date_str=e.get("event_date_str"),
                signup_deadline_str=None,
                category=e.get("category", "other"),
                score=int(e.get("score", 0)),
                score_reasoning=str(e.get("score_reasoning", ""))[:512] or None,
                notified=False,
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)

        top3 = sorted(rows, key=lambda r: r.score, reverse=True)[:3]
        for rank, evt in enumerate(top3, 1):
            date_info = f" on {evt.event_date_str}" if evt.event_date_str else ""
            loc_info = f" ({evt.location})" if evt.location else ""
            url_info = f"\n🔗 {evt.url}" if evt.url else ""
            content = (
                f"🎉 Event Pick #{rank} [{evt.category.upper()}] — {evt.title}{date_info}{loc_info}\n"
                f"{evt.description[:400]}\n"
                f"Relevance score: {evt.score}/100 — {evt.score_reasoning}{url_info}"
            )
            await create_notification(
                user_id=user_id,
                content=content[:2048],
                target_datetime=now,
            )
            evt.notified = True

        await session.commit()

    log.info("Persisted %d events, notified top-%d for user_id=%d", len(rows), len(top3), user_id)
    return rows


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def discover_events_for_user(user_id: int) -> dict:
    """Full pipeline: load scraped pages → build profile → LLM → persist → notify."""
    now = datetime.now(UTC)
    log.info("Starting event discovery for user_id=%d", user_id)

    pages = await _load_scraped_pages()
    if not pages:
        return {
            "total_events_found": 0,
            "top_events": [],
            "warning": "No scraped data available yet. The background scraper may still be running.",
        }

    import asyncio as _asyncio
    profile, cognee_ctx = await _asyncio.gather(
        _build_user_profile(user_id),
        _cognee_user_context(user_id),
    )

    events = await _recommend_events(pages, profile, cognee_ctx)
    persisted = await _persist_and_notify(user_id, events, now)

    top3 = sorted(persisted, key=lambda r: r.score, reverse=True)[:3]
    return {
        "total_events_found": len(persisted),
        "top_events": [
            {
                "id": e.id,
                "title": e.title,
                "category": e.category,
                "score": e.score,
                "score_reasoning": e.score_reasoning,
                "event_date": e.event_date_str,
                "location": e.location,
                "url": e.url,
            }
            for e in top3
        ],
    }
