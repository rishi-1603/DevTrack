"""User profile and admin user-management endpoints."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_admin
from app.core.logging import get_logger
from app.database.models import User
from app.database.session import get_db
from app.schemas.user import UserRead, UserUpdate
from app.utils.exceptions import NotFoundException

router = APIRouter(tags=["users"])
logger = get_logger("users_api")


@router.get("/users/me", response_model=UserRead)
def get_my_profile(current_user: User = Depends(get_current_user)) -> User:
    """Return the current authenticated user's profile."""
    return current_user


@router.put("/users/me", response_model=UserRead)
def update_my_profile(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """Update the current authenticated user's profile."""
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> None:
    """Delete a user account. Admin only."""
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundException("User not found.")
    db.delete(user)
    db.commit()
    logger.info("User id=%s deleted by admin_id=%s", user_id, _admin.id)
