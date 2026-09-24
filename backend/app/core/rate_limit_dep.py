"""FastAPI dependencies that apply Redis-backed rate limits to specific routes.

Two scoping strategies are used, matching the threat each endpoint faces:
  - `rate_limit_by_ip`: for unauthenticated endpoints (register/login), where
    the only identity available before authentication succeeds is the
    client's IP. This is what actually slows down credential-stuffing /
    brute-force attempts.
  - `rate_limit_by_user`: for authenticated endpoints (issue/comment
    creation, exports), scoped to the logged-in user, to stop one abusive
    account from spamming the API without punishing every other user
    sharing the same IP (e.g. behind NAT/a corporate proxy).
"""
from fastapi import Depends, Request

from app.core.dependencies import get_current_user
from app.database.models import User
from app.utils.exceptions import RateLimitExceededException
from app.utils.rate_limit import is_allowed


def rate_limit_by_ip(action: str, limit: int, window_seconds: int):
    """Build a dependency that rate-limits by client IP for a named action."""

    def _dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = is_allowed(f"ratelimit:{action}:ip:{client_ip}", limit, window_seconds)
        if not allowed:
            raise RateLimitExceededException(retry_after)

    return _dependency


def rate_limit_by_user(action: str, limit: int, window_seconds: int):
    """Build a dependency that rate-limits by authenticated user id for a named action.

    Depends on get_current_user, so this can only be used on routes that
    already require authentication.
    """

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        allowed, retry_after = is_allowed(f"ratelimit:{action}:user:{current_user.id}", limit, window_seconds)
        if not allowed:
            raise RateLimitExceededException(retry_after)
        return current_user

    return _dependency

