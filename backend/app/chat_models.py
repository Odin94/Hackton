from __future__ import annotations

from pydantic import BaseModel

from agent.models import ChatMessage
from app.chat_service import serialize_chat_message


class ChatMessageResp(BaseModel):
    id: int
    user_id: int
    timestamp: str
    author: str
    sequence_number: int
    content: str
    processing_ms: int | None = None


def to_chat_message_resp(
    message: ChatMessage,
    *,
    processing_ms: int | None = None,
) -> ChatMessageResp:
    return ChatMessageResp(
        **serialize_chat_message(message),
        processing_ms=processing_ms,
    )
