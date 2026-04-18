from __future__ import annotations

from fastapi import HTTPException

from app.auth import get_user_id


def require_bearer_user_id(authorization: str | None) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization token")

    token = authorization.removeprefix("Bearer ").strip()
    user_id = get_user_id(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user_id
