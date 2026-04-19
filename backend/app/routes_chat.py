"""Minimal authenticated chat API."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.api_auth import require_bearer_user_id
from app.chat_models import ChatMessageResp, to_chat_message_resp
from app.chat_service import (
    activate_demo_conversation,
    create_chat_reply,
    list_chat_messages,
)
from app.cognee_service import CogneeServiceError

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatHistoryResp(BaseModel):
    messages: list[ChatMessageResp]


class ChatReq(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ChatReplyResp(BaseModel):
    user_message: ChatMessageResp
    assistant_message: ChatMessageResp


class DemoTriggerReq(BaseModel):
    course_name: str = Field(min_length=1, max_length=256)


class DemoTriggerResp(BaseModel):
    notification_message: ChatMessageResp | None = None


@router.get("/chat/history", response_model=ChatHistoryResp, summary="Load chat history")
async def get_chat_history(authorization: str | None = Header(default=None)) -> ChatHistoryResp:
    user_id = require_bearer_user_id(authorization)
    messages = await list_chat_messages(user_id)
    return ChatHistoryResp(messages=[to_chat_message_resp(message) for message in messages])


@router.post("/chat/messages", response_model=ChatReplyResp, summary="Send a chat message")
async def post_chat_message(
    req: ChatReq,
    authorization: str | None = Header(default=None),
) -> ChatReplyResp:
    user_id = require_bearer_user_id(authorization)
    started_at = time.perf_counter()
    log.debug(
        "POST /chat/messages user_id=%d content_len=%d preview=%r",
        user_id,
        len(req.content),
        req.content[:160],
    )
    try:
        user_message, assistant_message = await create_chat_reply(user_id, req.content)
    except ValueError as e:
        log.warning("POST /chat/messages validation_error user_id=%d error=%s", user_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CogneeServiceError as e:
        status = 503 if e.retryable else 500
        log.warning(
            "POST /chat/messages cognee_error user_id=%d retryable=%s status=%d error=%s",
            user_id,
            e.retryable,
            status,
            e,
        )
        raise HTTPException(status_code=status, detail=str(e)) from e
    except Exception:
        log.exception("POST /chat/messages unexpected_error user_id=%d", user_id)
        raise

    processing_ms = int((time.perf_counter() - started_at) * 1000)
    log.debug(
        "POST /chat/messages timing user_id=%d user_message_id=%d assistant_message_id=%d processing_ms=%d",
        user_id,
        user_message.id,
        assistant_message.id,
        processing_ms,
    )
    log.info("chat turn stored user_id=%d user_seq=%d system_seq=%d", user_id, user_message.sequence_number, assistant_message.sequence_number)
    return ChatReplyResp(
        user_message=to_chat_message_resp(user_message),
        assistant_message=to_chat_message_resp(assistant_message, processing_ms=processing_ms),
    )


@router.post("/chat/demo-trigger", response_model=DemoTriggerResp, summary="Trigger the scripted demo flow")
async def post_chat_demo_trigger(
    req: DemoTriggerReq,
    authorization: str | None = Header(default=None),
) -> DemoTriggerResp:
    user_id = require_bearer_user_id(authorization)
    try:
        _, delivered_messages = await activate_demo_conversation(
            user_id,
            course_name=req.course_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    notification_message = delivered_messages[-1] if delivered_messages else None
    return DemoTriggerResp(
        notification_message=(
            to_chat_message_resp(notification_message) if notification_message is not None else None
        )
    )
