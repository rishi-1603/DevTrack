"""Schemas for Notification resources."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.database.models import NotificationType


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: NotificationType
    message: str
    issue_id: int | None
    is_read: bool
    created_at: datetime


class NotificationList(BaseModel):
    total: int
    unread_count: int
    items: list[NotificationRead]
