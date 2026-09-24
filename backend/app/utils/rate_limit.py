"""Redis-backed fixed-window rate limiter.

Design and trade-offs (worth stating explicitly, not hiding):

  - Algorithm: fixed window counter (INCR + EXPIRE on a key scoped to the
    current window), not a sliding window or token bucket. This is the
    simplest correct approach and is good enough here -- it can allow a
    short burst right at a window boundary (up to ~2x the limit across two
    adjacent windows), which is an accepted trade-off for the complexity
    saved. A sliding-window-log or token-bucket algorithm would remove that
    edge case at the cost of more Redis round-trips / more state.

  - Failure mode: THIS FAILS OPEN. If Redis is unreachable, `is_allowed()`
    returns True (request allowed) rather than blocking all traffic. This
    matches the existing app/utils/cache.py philosophy of "the app must
    never go down because Redis is down" -- but it is a real security
    trade-off worth naming out loud: during a Redis outage, brute-force
    protection on /auth/login is temporarily gone. The alternative
    (fail-closed) would turn a Redis blip into a full login outage for
    every user, which is a worse availability trade for a project at this
    scale. A production system handling real financial/PII data might
    choose to fail closed specifically for auth endpoints; that's a
    conscious choice not made here, documented rather than hidden.
"""
import time

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("rate_limit")

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


def is_allowed(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds).

    `key` should already be scoped to both the caller (IP or user id) and
    the action being limited, e.g. "ratelimit:login:203.0.113.5" -- this
    function does not add any scoping of its own.
    """
    window = int(time.time()) // window_seconds
    redis_key = f"{key}:{window}"

    try:
        client = _get_client()
        pipe = client.pipeline()
        pipe.incr(redis_key, 1)
        pipe.expire(redis_key, window_seconds)
        count, _ = pipe.execute()
    except (redis.exceptions.RedisError, ConnectionError, OSError) as exc:
        logger.warning("Redis unavailable for rate limit check on %s: %s -- failing open", key, exc)
        return True, 0

    if count > limit:
        # Time remaining until the current fixed window rolls over.
        retry_after = window_seconds - (int(time.time()) % window_seconds)
        return False, retry_after

    return True, 0
