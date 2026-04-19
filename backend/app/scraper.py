"""Web scraper for event discovery.

Scrapes EventScrapingTarget URLs (and all links found on them, 1 level deep)
using BeautifulSoup, stores results in EventScrapingResult with a 7-day expiry.

Intended usage
--------------
- `ensure_default_targets()` — idempotent insert of known portals, call on startup
- `scrape_stale_targets()` — background task; scrapes whatever has expired
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import delete, select

from agent.database import AsyncSessionLocal
from agent.models import EventScrapingResult, EventScrapingTarget

log = logging.getLogger(__name__)

DEFAULT_TARGETS = [
    "https://www.studierendenwerk-muenchen-oberbayern.de/kulturangebot/veranstaltungsprogramm/",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
_MAX_SUB_LINKS = 60
_MAX_TEXT_CHARS = 4000  # per page stored in DB


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------


async def ensure_default_targets() -> None:
    """Insert DEFAULT_TARGETS into the DB if they are not already present."""
    async with AsyncSessionLocal() as session:
        for url in DEFAULT_TARGETS:
            exists = await session.scalar(
                select(EventScrapingTarget.id).where(EventScrapingTarget.url == url).limit(1)
            )
            if exists is None:
                session.add(EventScrapingTarget(url=url, scrape_interval_days=7))
                log.info("Registered scraping target: %s", url)
        await session.commit()


async def scrape_stale_targets() -> None:
    """Find and scrape all targets whose data has expired (or was never scraped)."""
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        targets = (await session.execute(select(EventScrapingTarget))).scalars().all()

    def _is_stale(t: EventScrapingTarget) -> bool:
        if t.last_scraped_at is None:
            return True
        ts = t.last_scraped_at
        # SQLite returns naive datetimes — treat as UTC
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (now - ts) >= timedelta(days=t.scrape_interval_days)

    stale = [t for t in targets if _is_stale(t)]

    if not stale:
        log.info("All scraping targets are fresh — nothing to scrape")
        return

    log.info("Scraping %d stale target(s)", len(stale))
    for target in stale:
        try:
            await _scrape_target(target)
        except Exception:
            log.exception("Failed to scrape target %s", target.url)


# ---------------------------------------------------------------------------
# Core scraping (runs blocking I/O in thread)
# ---------------------------------------------------------------------------


def _extract_text(soup: BeautifulSoup) -> str:
    """Return clean visible text from a BeautifulSoup document."""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return "\n".join(
        line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()
    )


def _collect_sub_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Return unique same-domain links found on the page (no fragments, no base URL itself)."""
    base_parsed = urlparse(base_url)
    seen: set[str] = set()
    links: list[str] = []

    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        full = urljoin(base_url, href).split("#")[0].rstrip("/")
        parsed = urlparse(full)

        if (
            parsed.scheme in ("http", "https")
            and parsed.netloc == base_parsed.netloc
            and full != base_url.rstrip("/")
            and full not in seen
        ):
            seen.add(full)
            links.append(full)

    return links[:_MAX_SUB_LINKS]


def _scrape_sync(target_url: str) -> list[dict]:
    """Blocking scrape of target URL + 1 level of sub-links. Returns list of page dicts."""
    results: list[dict] = []

    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=30) as client:
        # -- Main page --
        resp = client.get(target_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        text = _extract_text(soup)
        title = soup.title.get_text(strip=True) if soup.title else None
        results.append({"url": target_url, "title": title, "text": text[:_MAX_TEXT_CHARS]})

        sub_links = _collect_sub_links(soup, target_url)
        log.info("Found %d sub-links on %s", len(sub_links), target_url)

        # -- Sub-pages (1 level deep) --
        for link in sub_links:
            try:
                r = client.get(link)
                if not r.is_success:
                    continue
                s = BeautifulSoup(r.text, "lxml")
                t = _extract_text(s)
                ttl = s.title.get_text(strip=True) if s.title else None
                results.append({"url": link, "title": ttl, "text": t[:_MAX_TEXT_CHARS]})
            except Exception as exc:
                log.warning("Skipping %s — %s", link, exc)

    log.info("Scraped %d page(s) for %s", len(results), target_url)
    return results


async def _scrape_target(target: EventScrapingTarget) -> None:
    """Orchestrate scraping for one target and persist results."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=target.scrape_interval_days)

    pages = await asyncio.to_thread(_scrape_sync, target.url)

    async with AsyncSessionLocal() as session:
        # Wipe old results for this target
        await session.execute(
            delete(EventScrapingResult).where(EventScrapingResult.target_id == target.id)
        )
        for p in pages:
            session.add(
                EventScrapingResult(
                    target_id=target.id,
                    url=p["url"],
                    title=p["title"],
                    text_content=p["text"],
                    scraped_at=now,
                    expires_at=expires_at,
                )
            )
        # Refresh the target's last_scraped_at
        fresh_target = await session.get(EventScrapingTarget, target.id)
        if fresh_target:
            fresh_target.last_scraped_at = now
        await session.commit()

    log.info("Persisted %d scraped pages for target %s", len(pages), target.url)
