"""DevTrack FastAPI application entrypoint."""
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, comments, dashboard, issues, projects, users
from app.core.config import settings
from app.core.logging import get_access_logger, get_logger, setup_logging
from app.database import models  # noqa: F401  (ensures models are registered on metadata)
from app.database.session import Base as DeclarativeBase
from app.database.session import engine
from app.utils.exceptions import AppException

setup_logging()
logger = get_logger("main")
access_logger = get_access_logger()

app = FastAPI(
    title=settings.APP_NAME,
    description="A lightweight issue-tracking backend (simplified Jira/Trello clone).",
    version="1.0.0",
)

origins = ["*"] if settings.CORS_ORIGINS == "*" else [o.strip() for o in settings.CORS_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """Log every request's method, path, status code, and duration to logs/access.log."""
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000
    access_logger.info(
        "%s %s %s %.2fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Translate custom application exceptions into consistent JSON error responses."""
    if exc.status_code >= 500:
        logger.error("Unhandled application error on %s %s: %s", request.method, request.url.path, exc.detail)
    else:
        logger.info("Handled error on %s %s: %s", request.method, request.url.path, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.__class__.__name__, "detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler so unexpected errors still return a consistent JSON body."""
    logger.error("Unexpected error on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "detail": "An unexpected error occurred."},
    )


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(issues.router)
app.include_router(comments.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["health"])
def health_check() -> dict:
    """Simple liveness probe."""
    return {"status": "ok", "app": settings.APP_NAME}


@app.on_event("startup")
def on_startup() -> None:
    """Create tables automatically only in local/dev SQLite runs; Postgres uses Alembic migrations."""
    if settings.DATABASE_URL.startswith("sqlite"):
        DeclarativeBase.metadata.create_all(bind=engine)
    logger.info("%s application startup complete.", settings.APP_NAME)
