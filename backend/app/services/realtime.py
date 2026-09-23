"""Bridges synchronous service-layer code to the async WebSocket connection manager.

Why this exists: DevTrack's API routes are plain `def` (sync) handlers, which
FastAPI runs in a worker thread pool -- there is no asyncio event loop running
in that thread. The actual WebSocket connections, however, live on the
server's *main* event loop (the one Uvicorn runs). To push a message to a
connected client from a sync worker thread, we need to schedule the coroutine
onto that main loop from another thread, which is exactly what
`asyncio.run_coroutine_threadsafe` is for.

`app.main` captures a reference to the running loop at startup
(`realtime.set_event_loop(...)`). If no loop has been captured yet (e.g. in
plain unit tests that never start Uvicorn, or before startup has run), the
live-push step is skipped -- the notification is still safely persisted to
PostgreSQL by `notification_service.create_notification`, so nothing is lost;
the user just sees it next time they call GET /notifications instead of
instantly. This graceful-degradation behavior is intentional and mirrors the
existing Redis fail-open pattern already used in `utils/cache.py`.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.ws_manager import manager
from app.database.models import NotificationType
from app.services import notification_service

logger = get_logger("realtime")

_event_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from the FastAPI startup event to capture the main loop."""
    global _event_loop
    _event_loop = loop


def notify_user(
    db: Session,
    user_id: int,
    notification_type: NotificationType,
    message: str,
    issue_id: int | None = None,
) -> None:
    """Persist a notification and best-effort push it live over WebSocket.

    Always persists first. The live push is fire-and-forget: if it fails or
    there is no event loop registered (e.g. under pytest), we log and move on
    without raising, so a notification failure can never break the request
    that triggered it (e.g. assigning an issue must still succeed).
    """
    notification = notification_service.create_notification(
        db, user_id=user_id, notification_type=notification_type, message=message, issue_id=issue_id
    )

    if _event_loop is None or _event_loop.is_closed():
        logger.debug("No usable event loop registered; skipping live push for user_id=%s", user_id)
        return

    payload = {
        "id": notification.id,
        "type": notification.type.value,
        "message": notification.message,
        "issue_id": notification.issue_id,
        "created_at": notification.created_at.isoformat(),
    }

    coro = manager.push_to_user(user_id, payload)
    try:
        asyncio.run_coroutine_threadsafe(coro, _event_loop)
    except Exception as exc:  # noqa: BLE001 - live push must never break the caller
        # If scheduling itself failed (e.g. the loop closed between the check
        # above and this call), the coroutine object was created but never
        # submitted to a loop -- explicitly close it to avoid a Python
        # "coroutine was never awaited" ResourceWarning leaking into logs.
        coro.close()
        logger.warning("Failed to schedule live push for user_id=%s: %s", user_id, exc)
