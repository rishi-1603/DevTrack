"""WebSocket endpoint for real-time notifications.

Auth: browsers cannot set an `Authorization` header on a WebSocket handshake,
so the JWT access token is passed as a query parameter (`?token=...`) instead
-- the standard workaround for this well-known WebSocket limitation. The
token is validated with the exact same `decode_token` used for REST auth, so
it expires and is rejected on the same schedule as regular API tokens.

Protocol: on connect, the server immediately sends any unread notifications
(so refreshing the page or reconnecting doesn't lose anything), then keeps
the socket open and pushes new notification events as they are created
elsewhere in the app (see app/services/realtime.py). The client does not need
to send anything after connecting; the server ignores/ping-pongs incoming
frames just to detect disconnects promptly.
"""
import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import decode_token
from app.core.ws_manager import manager
from app.database.models import User
from app.database.session import get_db
from app.services import notification_service, realtime

router = APIRouter(tags=["realtime"])
logger = get_logger("ws_api")


def _authenticate_ws_token(token: str, db: Session) -> User | None:
    try:
        payload = decode_token(token)
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    return db.get(User, int(user_id))


@router.websocket("/ws/notifications")
async def notifications_ws(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> None:
    # Belt-and-suspenders: register the currently-running event loop here too
    # (in addition to app.main's startup hook). A WebSocket handler always
    # executes on a real running loop, so this guarantees realtime.notify_user
    # can schedule live pushes even if the FastAPI "startup" lifecycle event
    # never fired for some reason (e.g. certain test-client usage patterns).
    realtime.set_event_loop(asyncio.get_running_loop())

    # Uses the same `get_db` dependency as every REST endpoint (rather than
    # opening a raw SessionLocal()) so that it participates correctly in
    # FastAPI's dependency-override mechanism -- this is what lets the test
    # suite exercise the WebSocket against the isolated in-memory test
    # database instead of accidentally hitting the real Postgres connection
    # string from app.core.config.settings.
    user = _authenticate_ws_token(token, db)
    if user is None:
        await websocket.close(code=4401)  # custom close code: unauthorized
        return

    await manager.connect(user.id, websocket)
    try:
        items, unread_count = notification_service.list_notifications(db, user, unread_only=True)
        await websocket.send_json(
            {
                "event": "unread_backlog",
                "unread_count": unread_count,
                "items": [
                    {
                        "id": n.id,
                        "type": n.type.value,
                        "message": n.message,
                        "issue_id": n.issue_id,
                        "created_at": n.created_at.isoformat(),
                    }
                    for n in items
                ],
            }
        )
        while True:
            # We don't require the client to send anything; this just
            # blocks until the client disconnects (or sends a frame, which we
            # discard). Detecting disconnect here is what lets us clean up
            # the registry promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user.id, websocket)
