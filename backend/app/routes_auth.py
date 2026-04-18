"""Auth routes — signup and login.

No passwords.  You just claim a unique username; the server hands back a token
that lets you open a WebSocket.  Designed for local demo use only.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from agent.database import AsyncSessionLocal
from agent.models import User
from app.auth import create_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class UsernameReq(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("username cannot be empty")
        if len(v) > 64:
            raise ValueError("username must be 64 characters or fewer")
        return v


class AuthResp(BaseModel):
    token: str
    user_id: int
    username: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=AuthResp, summary="Create a new account")
async def signup(req: UsernameReq) -> AuthResp:
    """Register a new user by username.  Returns a bearer token."""
    log.debug("POST /signup username=%r", req.username)
    async with AsyncSessionLocal() as session:
        log.debug("POST /signup: checking for existing username=%r", req.username)
        clash = (
            await session.execute(select(User).where(User.username == req.username))
        ).scalar_one_or_none()

        if clash is not None:
            log.debug("POST /signup: username already taken username=%r existing_id=%d", req.username, clash.id)
            raise HTTPException(
                status_code=409, detail=f"Username '{req.username}' is already taken."
            )

        user = User(username=req.username)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    log.info("New user signed up: username=%s id=%d", user.username, user.id)
    log.debug("POST /signup → token minted for user_id=%d", user.id)
    return AuthResp(token=create_token(user.id), user_id=user.id, username=user.username)


@router.post("/login", response_model=AuthResp, summary="Log in with your username")
async def login(req: UsernameReq) -> AuthResp:
    """Identify yourself by username.  Returns a fresh bearer token."""
    log.debug("POST /login username=%r", req.username)
    async with AsyncSessionLocal() as session:
        log.debug("POST /login: looking up username=%r", req.username)
        user = (
            await session.execute(select(User).where(User.username == req.username))
        ).scalar_one_or_none()

    if user is None:
        log.debug("POST /login: username not found username=%r", req.username)
        raise HTTPException(status_code=404, detail=f"No user with username '{req.username}'.")

    log.info("User logged in: username=%s id=%d", user.username, user.id)
    log.debug("POST /login → token minted for user_id=%d", user.id)
    return AuthResp(token=create_token(user.id), user_id=user.id, username=user.username)
