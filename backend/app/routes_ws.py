"""WebSocket endpoint.

Flow
----
1. Client calls POST /login  →  receives ``token``.
2. Client connects to  ``ws://host/ws?token=<token>``.
3. Server validates the token, registers the connection, then loops waiting
   for messages (any text is echoed back as ``{"type": "ack", "echo": …}``).
4. On disconnect the connection is removed from the registry so the scheduler
   will no longer attempt to push to this user.
"""

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.auth import get_user_id
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
        while True:
            # Keep the socket alive; handle any text the client sends.
            text = await ws.receive_text()
            log.debug("WS recv user_id=%d  %.120s", user_id, text)
            await ws.send_json({"type": "ack", "echo": text})
            log.debug("WS ack sent user_id=%d", user_id)
    except WebSocketDisconnect:
        log.info("WS client disconnected user_id=%d", user_id)
    finally:
        manager.disconnect(user_id)
