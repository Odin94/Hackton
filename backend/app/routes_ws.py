"""WebSocket endpoint.

Flow
----
1. Client calls POST /login  →  receives ``token``.
2. Client connects to  ``ws://host/ws?token=<token>``.
3. Server validates the token, registers the connection, flushes any due
   notifications for that user as chat-style system messages, then loops
   waiting for messages (any text is echoed back as ``{"type": "ack", "echo": …}``).
4. On disconnect the connection is removed from the registry so the scheduler
   will no longer attempt to push to this user.
"""

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.auth import get_user_id
from app.chat_service import deliver_due_notifications_as_chat_messages, serialize_chat_message
from app.connection_manager import manager

log = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str = Query(..., description="Token returned by /login or /signup"),
) -> None:
    log.debug("WS connection attempt token_prefix=%s", token[:8] if token else "(empty)")
    user_id = get_user_id(token)
    if user_id is None:
        log.warning("WS rejected: invalid or expired token token_prefix=%s", token[:8] if token else "(empty)")
        # Reject before accepting — code 4001 = "unauthorised" by convention
        await ws.close(code=4001, reason="invalid or expired token")
        return

    log.debug("WS token validated user_id=%d", user_id)
    await manager.connect(user_id, ws)
    try:
        delivered = await deliver_due_notifications_as_chat_messages(user_id)
        log.debug(
            "WS connect flush user_id=%d delivered_count=%d delivered_message_ids=%s",
            user_id,
            len(delivered),
            [message.id for message in delivered],
        )
        for message in delivered:
            payload = {
                "type": "chat_message",
                "message": serialize_chat_message(message),
            }
            log.debug(
                "WS send connect_message user_id=%d message_id=%d sequence=%d payload_keys=%s content_preview=%r",
                user_id,
                message.id,
                message.sequence_number,
                list(payload.keys()),
                (message.content[:120] + "...") if len(message.content) > 120 else message.content,
            )
            await ws.send_json(
                payload
            )
        if delivered:
            log.info("WS delivered %d due notification chat message(s) user_id=%d", len(delivered), user_id)
        else:
            log.debug("WS connect flush found no due notifications user_id=%d", user_id)

        while True:
            # Keep the socket alive; handle any text the client sends.
            text = await ws.receive_text()
            log.debug("WS recv user_id=%d text_len=%d preview=%r", user_id, len(text), text[:120])
            ack_payload = {"type": "ack", "echo": text}
            await ws.send_json(ack_payload)
            log.debug("WS ack sent user_id=%d payload_keys=%s", user_id, list(ack_payload.keys()))
    except WebSocketDisconnect:
        log.info("WS client disconnected user_id=%d", user_id)
    except Exception:
        log.exception("WS unexpected error user_id=%d", user_id)
        raise
    finally:
        manager.disconnect(user_id)
