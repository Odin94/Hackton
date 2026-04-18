import logging
from contextlib import asynccontextmanager

from app import config  # noqa: F401 — normalizes env before cognee loads

import cognee
from fastapi import FastAPI

from app.routes_cognee import router as cognee_router
from agent.db import init_db
from agent.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # cognee's alembic `ab7e313804ae_permission_system_rework` migration raises
    # NoSuchTableError("acls") on a fresh relational DB — the migration reads
    # column metadata before the base tables exist. Don't let that block boot;
    # cognee's pipeline `setup()` creates the schema on first add/cognify via
    # SQLAlchemy models directly, which works fine.
    try:
        await cognee.run_startup_migrations()
    except Exception as e:
        _log.warning(
            "cognee.run_startup_migrations() failed; continuing (schema will be "
            "created lazily on first add/cognify): %s",
            e,
        )
    await init_db()
    llm_task, dispatch_task = start_scheduler()
    yield
    llm_task.cancel()
    dispatch_task.cancel()


app = FastAPI(lifespan=lifespan, title="Study Diary backend")
app.include_router(cognee_router)
