"""Authentication endpoints: register, login, refresh, change password, logout."""
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.rate_limit_dep import rate_limit_by_ip
from app.database.models import User
from app.database.session import get_db
from app.schemas.token import ChangePasswordRequest, RefreshTokenRequest, Token
from app.schemas.user import UserCreate, UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

# Per-IP limits on the two credential-related endpoints attackers actually
# automate: unlimited /auth/register lets one client mass-create accounts
# (spam/abuse), and unlimited /auth/login is a brute-force oracle. Both are
# scoped by IP (see app/core/rate_limit_dep.py's docstring for why -- there
# is no authenticated user yet at this point in the request).
_login_rate_limit = rate_limit_by_ip("login", limit=10, window_seconds=60)
_register_rate_limit = rate_limit_by_ip("register", limit=5, window_seconds=60)


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_register_rate_limit)],
)
def register(user_in: UserCreate, db: Session = Depends(get_db)) -> User:
    """Create a new user account."""
    return auth_service.register_user(db, user_in)


@router.post("/login", response_model=Token, dependencies=[Depends(_login_rate_limit)])
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> Token:
    """Authenticate with email/password (OAuth2 form) and receive a JWT token pair."""
    user = auth_service.authenticate_user(db, form_data.username, form_data.password)
    return auth_service.issue_tokens_for_user(db, user)


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshTokenRequest, db: Session = Depends(get_db)) -> Token:
    """Exchange a valid refresh token for a new access/refresh token pair."""
    return auth_service.refresh_access_token(db, payload.refresh_token)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Change the current user's password."""
    auth_service.change_password(db, current_user, payload.current_password, payload.new_password)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    """Record a logout event. JWTs are stateless, so the client should discard its token."""
    auth_service.log_logout(db, current_user)
