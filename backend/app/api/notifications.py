"""REST endpoints for notifications (complements the WebSocket push channel).

These exist so that:
  - a client that hasn't connected the WebSocket yet still has a way to load
    notification history (e.g. a notification bell icon on page load),
  - notifications can be marked read from a normal HTTP call.
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.models import User
from app.database.session import get_db
from app.schemas.notification import NotificationList, NotificationRead
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
def list_notifications(
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, unread_count = notification_service.list_notifications(db, current_user, unread_only=unread_only)
    return NotificationList(total=len(items), unread_count=unread_count, items=items)


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return notification_service.mark_notification_read(db, notification_id, current_user)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    notification_service.mark_all_read(db, current_user)
