"""Business logic for Notification resources.

A notification is always written to PostgreSQL first (source of truth, so a
user who is offline still sees it later), and only *after* the DB commit
succeeds do we attempt a best-effort live push over WebSocket. This ordering
matters: if the WebSocket push happened first, a crash before the DB commit
would silently lose the notification for offline/reconnecting clients.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.models import Notification, NotificationType, User
from app.utils.exceptions import NotFoundException, PermissionDeniedException

logger = get_logger("notification_service")


def create_notification(
    db: Session,
    user_id: int,
    notification_type: NotificationType,
    message: str,
    issue_id: int | None = None,
) -> Notification:
    """Persist a notification. Caller is responsible for triggering the live push."""
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        message=message,
        issue_id=issue_id,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    logger.info("Notification created id=%s for user_id=%s type=%s", notification.id, user_id, notification_type.value)
    return notification


def list_notifications(db: Session, user: User, unread_only: bool = False) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc())
    items = list(db.scalars(stmt).all())

    unread_count = db.scalar(
        select(func.count()).select_from(
            select(Notification.id)
            .where(Notification.user_id == user.id, Notification.is_read.is_(False))
            .subquery()
        )
    ) or 0
    return items, unread_count


def mark_notification_read(db: Session, notification_id: int, user: User) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise NotFoundException("Notification not found.")
    if notification.user_id != user.id:
        raise PermissionDeniedException("You cannot modify another user's notification.")

    notification.is_read = True
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def mark_all_read(db: Session, user: User) -> int:
    stmt = select(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))
    unread = list(db.scalars(stmt).all())
    for notification in unread:
        notification.is_read = True
        db.add(notification)
    db.commit()
    return len(unread)
