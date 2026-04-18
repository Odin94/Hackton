import logging
from contextlib import asynccontextmanager

from app import config  # noqa: F401 — normalizes env before cognee loads
from app.config import settings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes_cognee import router as cognee_router
from app.routes_auth import router as auth_router
from app.routes_chat import router as chat_router
from app.routes_demo import router as demo_router
from app.routes_events import router as events_router
from app.routes_ws import router as ws_router
from agent.db import init_db
from agent.scheduler import start_scheduler
from app.demo_time import install_demo_clock
from app.scraper import ensure_default_targets, scrape_stale_targets

# Apply demo-clock monkey-patch AFTER all app modules have imported datetime.
# Every `from datetime import datetime` inside those modules becomes a module-
# level attribute we can patch; lambda/default_factory closures late-bind via
# module globals, so values generated later (SQLAlchemy column defaults,
# Pydantic default_factory) see the frozen time.
install_demo_clock(settings.current_date_override)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

log = logging.getLogger(__name__)


async def _init_cognee_db() -> None:
    """Create cognee's relational tables via SQLAlchemy create_all.

    cognee 1.0.0's alembic migration chain crashes on a fresh DB (the upgrade
    scripts inspect tables that haven't been created yet).  Bypassing the
    migration runner and using create_all instead is safe: it's idempotent,
    produces the correct current schema, and avoids the whole broken chain.
    """
    from cognee.infrastructure.databases.relational import get_relational_engine, Base

    sa_engine = get_relational_engine().engine
    async with sa_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Cognee DB tables ready")


_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_cognee_db()
    await init_db()
    await ensure_default_targets()
    # Scrape stale targets in the background — don't block startup
    import asyncio as _asyncio
    _asyncio.create_task(scrape_stale_targets())
    if settings.disable_scheduler:
        log.info("Scheduler disabled via DISABLE_SCHEDULER — demo mode")
        yield
        return
    llm_task, dispatch_task = start_scheduler()
    yield
    llm_task.cancel()
    dispatch_task.cancel()


app = FastAPI(lifespan=lifespan, title="Study Diary backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(ws_router)
app.include_router(chat_router)
app.include_router(demo_router)
app.include_router(events_router)
app.include_router(cognee_router)
