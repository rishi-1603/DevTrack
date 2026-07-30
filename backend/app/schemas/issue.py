"""Schemas for Issue resources."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import IssuePriority, IssueStatus
from app.schemas.user import UserPublic


class IssueBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    priority: IssuePriority = IssuePriority.MEDIUM
    due_date: datetime | None = None


class IssueCreate(IssueBase):
    project_id: int
    assigned_to: int | None = None


class IssueUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    priority: IssuePriority | None = None
    due_date: datetime | None = None
    assigned_to: int | None = None


class IssueStatusUpdate(BaseModel):
    status: IssueStatus


class IssueAssignRequest(BaseModel):
    user_id: int


class IssueRead(IssueBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: IssueStatus
    project_id: int
    assigned_to: int | None
    assignee: UserPublic | None = None
    created_at: datetime


class IssueList(BaseModel):
    total: int
    items: list[IssueRead]
