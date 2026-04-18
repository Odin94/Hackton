"""Minimal authenticated chat API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_user_id
from app.chat_service import create_chat_reply, list_chat_messages
from app.cognee_service import CogneeServiceError

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatMessageResp(BaseModel):
    id: int
    user_id: int
    timestamp: str
    author: str
    sequence_number: int
    content: str


class ChatHistoryResp(BaseModel):
    messages: list[ChatMessageResp]


class ChatReq(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ChatReplyResp(BaseModel):
    user_message: ChatMessageResp
    assistant_message: ChatMessageResp


def _require_user_id(authorization: str | None) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization token")

    token = authorization.removeprefix("Bearer ").strip()
    user_id = get_user_id(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user_id


def _serialize(message) -> ChatMessageResp:
    return ChatMessageResp(
        id=message.id,
        user_id=message.user_id,
        timestamp=message.timestamp.isoformat(),
        author=message.author,
        sequence_number=message.sequence_number,
        content=message.content,
    )


@router.get("/chat/history", response_model=ChatHistoryResp, summary="Load chat history")
async def get_chat_history(authorization: str | None = Header(default=None)) -> ChatHistoryResp:
    user_id = _require_user_id(authorization)
    messages = await list_chat_messages(user_id)
    return ChatHistoryResp(messages=[_serialize(message) for message in messages])


@router.post("/chat/messages", response_model=ChatReplyResp, summary="Send a chat message")
async def post_chat_message(
    req: ChatReq,
    authorization: str | None = Header(default=None),
) -> ChatReplyResp:
    user_id = _require_user_id(authorization)
    try:
        user_message, assistant_message = await create_chat_reply(user_id, req.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CogneeServiceError as e:
        status = 503 if e.retryable else 500
        raise HTTPException(status_code=status, detail=str(e)) from e

    log.info("chat turn stored user_id=%d user_seq=%d system_seq=%d", user_id, user_message.sequence_number, assistant_message.sequence_number)
    return ChatReplyResp(
        user_message=_serialize(user_message),
        assistant_message=_serialize(assistant_message),
    )
