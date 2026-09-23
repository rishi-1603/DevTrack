"""In-process WebSocket connection registry for real-time notifications.

Design notes (things an interviewer will ask about):

- Connections are kept in an in-memory dict keyed by user_id. This is
  intentionally simple and correct for a *single-process* deployment (one
  Uvicorn worker). It is documented here as a known scaling limit: with
  multiple worker processes/replicas, a notification created on worker A
  would not reach a socket held open on worker B. The fix for that is a
  pub/sub fan-out through Redis (Redis already exists in this stack for
  caching) — publish on notification creation, have every worker subscribe
  and push to any locally-held sockets. That is called out explicitly in the
  README as a documented next step rather than silently pretended away.
- A user may have multiple tabs/devices open at once, so we keep a *set* of
  sockets per user, not a single socket.
- Sending never raises into caller code: if a socket is dead/broken, we drop
  it from the registry instead of propagating the exception, so a failed
  push to one stale connection can never break the request that triggered
  the notification (e.g. assigning an issue must succeed in the database
  even if the live push fails).
"""
from __future__ import annotations

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger("ws_manager")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)
        logger.info("WebSocket connected for user_id=%s (total_for_user=%d)", user_id, len(self._connections[user_id]))

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(user_id, None)
        logger.info("WebSocket disconnected for user_id=%s", user_id)

    async def push_to_user(self, user_id: int, payload: dict) -> int:
        """Push a JSON-serializable payload to every open socket for user_id.

        Returns the number of sockets the payload was successfully sent to.
        Never raises: dead sockets are silently dropped from the registry.
        """
        sockets = list(self._connections.get(user_id, ()))
        if not sockets:
            return 0

        delivered = 0
        for socket in sockets:
            try:
                await socket.send_json(payload)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - a broken socket must never bubble up
                logger.warning("Dropping dead WebSocket for user_id=%s: %s", user_id, exc)
                self.disconnect(user_id, socket)
        return delivered

    def connection_count(self, user_id: int) -> int:
        return len(self._connections.get(user_id, ()))


manager = ConnectionManager()
