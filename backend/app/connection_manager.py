"""WebSocket connection manager.

A single module-level ``manager`` instance is the source of truth for active
connections.  Import it from both the WebSocket route and the notification
dispatcher so they share the same registry.

Only one connection per user is kept alive at a time.  A new connection for
an already-connected user silently replaces the old one (the old socket is
left to time-out naturally — the client is responsible for not opening
duplicates).
"""

import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> live WebSocket
        self._connections: dict[int, WebSocket] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        """Accept *ws* and register it for *user_id*."""
        log.debug("WS manager connect start user_id=%d replacing=%s", user_id, user_id in self._connections)
        await ws.accept()
        if user_id in self._connections:
            log.info("WS: replacing existing connection for user_id=%d", user_id)
        self._connections[user_id] = ws
        log.info("WS connected  user_id=%-5d  total_active=%d", user_id, len(self._connections))

    def disconnect(self, user_id: int) -> None:
        """Remove the connection for *user_id* (idempotent)."""
        removed = self._connections.pop(user_id, None)
        log.debug("WS manager disconnect user_id=%d existed=%s", user_id, removed is not None)
        log.info("WS disconnected user_id=%-5d  total_active=%d", user_id, len(self._connections))

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(self, user_id: int, payload: dict) -> bool:
        """Send JSON *payload* to *user_id*.

        Returns ``True`` if the message was sent, ``False`` if the user has no
        active connection (caller can decide whether to retry later).
        """
        ws = self._connections.get(user_id)
        if ws is None:
            log.debug("WS manager send skipped user_id=%d reason=no_connection payload_keys=%s", user_id, list(payload.keys()))
            return False
        log.debug("WS manager send start user_id=%d payload_keys=%s", user_id, list(payload.keys()))
        await ws.send_json(payload)
        log.debug("WS sent to user_id=%d  keys=%s", user_id, list(payload.keys()))
        return True

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def is_connected(self, user_id: int) -> bool:
        connected = user_id in self._connections
        log.debug("WS manager is_connected user_id=%d connected=%s", user_id, connected)
        return connected

    @property
    def active_user_ids(self) -> list[int]:
        return list(self._connections)


# Module-level singleton — the single source of truth for active connections.
manager = ConnectionManager()
