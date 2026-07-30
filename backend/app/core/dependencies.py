"""Shared FastAPI dependencies: DB session, current user, RBAC guards."""
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database.models import User, UserRole
from app.database.session import get_db
from app.utils.exceptions import InvalidTokenException, PermissionDeniedException

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode the bearer token, validate it is an access token, and load the user."""
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise InvalidTokenException("Could not validate credentials.") from exc

    if payload.get("type") != "access":
        raise InvalidTokenException("Expected an access token.")

    user_id = payload.get("sub")
    if user_id is None:
        raise InvalidTokenException("Token is missing a subject.")

    user = db.get(User, int(user_id))
    if user is None:
        raise InvalidTokenException("User no longer exists.")

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that only allows admin users through."""
    if current_user.role != UserRole.ADMIN:
        raise PermissionDeniedException("Admin privileges are required for this action.")
    return current_user
