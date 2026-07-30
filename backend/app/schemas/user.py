"""Schemas for User resources."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.database.models import UserRole


class UserBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: UserRole
    created_at: datetime


class UserPublic(BaseModel):
    """Minimal user info safe to nest inside other resources."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
