"""Schemas for Project resources."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserPublic


class ProjectBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    owner: UserPublic
    created_at: datetime


class ProjectList(BaseModel):
    total: int
    items: list[ProjectRead]
