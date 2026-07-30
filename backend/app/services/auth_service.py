"""Business logic for authentication: register, login, refresh, change password."""
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database.models import ActivityLog, User, UserRole
from app.schemas.token import Token
from app.schemas.user import UserCreate
from app.utils.exceptions import DuplicateException, InvalidCredentialsException, InvalidTokenException

logger = get_logger("auth_service")


def _log_activity(db: Session, user_id: int, action: str) -> None:
    db.add(ActivityLog(user_id=user_id, action=action))
    db.commit()


def register_user(db: Session, user_in: UserCreate) -> User:
    """Create a new user account with a hashed password and default 'developer' role."""
    existing = db.scalar(select(User).where(User.email == user_in.email))
    if existing is not None:
        raise DuplicateException("A user with this email already exists.")

    user = User(
        name=user_in.name,
        email=user_in.email,
        password_hash=hash_password(user_in.password),
        role=UserRole.DEVELOPER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _log_activity(db, user.id, "registered")
    logger.info("New user registered: %s (id=%s)", user.email, user.id)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    """Verify credentials and return the matching user, or raise InvalidCredentialsException."""
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        logger.warning("Failed login attempt for email=%s", email)
        raise InvalidCredentialsException("Incorrect email or password.")
    return user


def issue_tokens_for_user(db: Session, user: User, log_login: bool = True) -> Token:
    """Issue a fresh access/refresh token pair for a user."""
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    if log_login:
        _log_activity(db, user.id, "logged in")
        logger.info("User logged in: %s (id=%s)", user.email, user.id)
    return Token(access_token=access_token, refresh_token=refresh_token)


def refresh_access_token(db: Session, refresh_token: str) -> Token:
    """Validate a refresh token and issue a new access/refresh token pair."""
    try:
        payload = decode_token(refresh_token)
    except JWTError as exc:
        raise InvalidTokenException("Invalid or expired refresh token.") from exc

    if payload.get("type") != "refresh":
        raise InvalidTokenException("Expected a refresh token.")

    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id is not None else None
    if user is None:
        raise InvalidTokenException("User no longer exists.")

    return issue_tokens_for_user(db, user, log_login=False)


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Change a user's password after verifying their current one."""
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsException("Current password is incorrect.")

    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()

    _log_activity(db, user.id, "changed password")
    logger.info("User changed password: %s (id=%s)", user.email, user.id)


def log_logout(db: Session, user: User) -> None:
    """Record a logout event in the activity log."""
    _log_activity(db, user.id, "logged out")
    logger.info("User logged out: %s (id=%s)", user.email, user.id)
