"""Schemas for Comment resources."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserPublic


class CommentBase(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


class CommentCreate(CommentBase):
    pass


class CommentRead(CommentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_id: int
    user_id: int
    user: UserPublic
    created_at: datetime
