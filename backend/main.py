import logging
from contextlib import asynccontextmanager

from app import config  # noqa: F401 — normalizes env before cognee loads

import cognee
from fastapi import FastAPI

from app.routes_cognee import router as cognee_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cognee.run_startup_migrations()
    yield


app = FastAPI(lifespan=lifespan, title="Study Diary backend")
app.include_router(cognee_router)
