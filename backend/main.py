import logging
from contextlib import asynccontextmanager

from app import config  # noqa: F401 — normalizes env before cognee loads

import cognee
from fastapi import FastAPI

from app.routes_cognee import router as cognee_router
from agent.db import init_db
from agent.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cognee.run_startup_migrations()
    await init_db()
    llm_task, dispatch_task = start_scheduler()
    yield
    llm_task.cancel()
    dispatch_task.cancel()


app = FastAPI(lifespan=lifespan, title="Study Diary backend")
app.include_router(cognee_router)
