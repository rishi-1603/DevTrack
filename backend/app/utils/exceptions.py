"""Custom application exceptions.

These are raised from the service layer and translated into consistent JSON
responses by the global exception handlers registered in app.main.
"""


class AppException(Exception):
    """Base class for all application-level exceptions."""

    status_code: int = 500
    detail: str = "An unexpected error occurred."

    def __init__(self, detail: str | None = None, headers: dict[str, str] | None = None) -> None:
        if detail is not None:
            self.detail = detail
        self.headers = headers
        super().__init__(self.detail)


class RateLimitExceededException(AppException):
    """Raised when a Redis-backed rate limit (see app/core/rate_limit_dep.py)
    is exceeded. Carries a Retry-After header so well-behaved clients know
    exactly how long to back off."""

    status_code = 429
    detail = "Too many requests."

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            detail=f"Too many requests. Try again in {retry_after_seconds} seconds.",
            headers={"Retry-After": str(retry_after_seconds)},
        )
        self.retry_after_seconds = retry_after_seconds



class NotFoundException(AppException):
    status_code = 404
    detail = "Resource not found."


class PermissionDeniedException(AppException):
    status_code = 403
    detail = "You do not have permission to perform this action."


class DuplicateException(AppException):
    status_code = 409
    detail = "Resource already exists."


class InvalidCredentialsException(AppException):
    status_code = 401
    detail = "Invalid credentials."


class InvalidTokenException(AppException):
    status_code = 401
    detail = "Invalid or expired token."


class BadRequestException(AppException):
    status_code = 400
    detail = "Bad request."
