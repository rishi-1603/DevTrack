"""Thin Redis caching helper.

The rest of the app must never crash if Redis is unavailable: every method
here catches connection errors and degrades gracefully (cache miss / no-op).
"""
import json
from typing import Any

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("cache")

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
    return _client


def get_cache(key: str) -> Any | None:
    """Return the cached JSON value for `key`, or None on miss / Redis failure."""
    try:
        client = _get_client()
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except (redis.exceptions.RedisError, ConnectionError, OSError) as exc:
        logger.warning("Redis unavailable on GET %s: %s", key, exc)
        return None


def set_cache(key: str, value: Any, ttl_seconds: int) -> None:
    """Store `value` as JSON under `key` with a TTL. No-ops if Redis is unavailable."""
    try:
        client = _get_client()
        client.set(key, json.dumps(value), ex=ttl_seconds)
    except (redis.exceptions.RedisError, ConnectionError, OSError, TypeError) as exc:
        logger.warning("Redis unavailable on SET %s: %s", key, exc)


def delete_cache(key: str) -> None:
    """Delete a cache key. No-ops if Redis is unavailable."""
    try:
        client = _get_client()
        client.delete(key)
    except (redis.exceptions.RedisError, ConnectionError, OSError) as exc:
        logger.warning("Redis unavailable on DELETE %s: %s", key, exc)
