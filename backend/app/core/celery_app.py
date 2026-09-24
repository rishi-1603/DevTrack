"""Celery application instance, using Redis as both broker and result backend.

Reuses the same Redis instance/settings as app/utils/cache.py -- no new
infrastructure is introduced, just a second use of the Redis that was
already required for dashboard caching and rate limiting.

Why Celery at all (vs. e.g. FastAPI BackgroundTasks): BackgroundTasks run
in-process, in the same worker that handled the HTTP request -- if that
worker process restarts or is one of several behind a load balancer, an
in-flight background task can silently vanish, and a slow task still ties
up that process's resources. Celery workers are separate processes (can run
on separate machines) with a durable broker queue in front of them, so a
CSV export survives an API server restart/redeploy and doesn't compete with
the API for the same process's CPU/memory. That durability is the entire
justification for the extra moving part -- it is not used for anything that
BackgroundTasks would already handle fine.
"""
from celery import Celery

from app.core.config import settings


def _redis_url() -> str:
    auth = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
    return f"redis://{auth}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"


celery_app = Celery(
    "devtrack",
    broker=_redis_url(),
    backend=_redis_url(),
    include=["app.tasks.export_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # A stuck export (e.g. an infinite loop bug) should not tie up a worker
    # forever -- Celery hard-kills the task after this many seconds.
    task_time_limit=300,
    # Tests set this to True (see app/tests/conftest.py) so tasks execute
    # synchronously in-process instead of requiring a real Redis broker and
    # a running worker.
    task_always_eager=False,
)
