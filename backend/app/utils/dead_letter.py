"""Redis-backed dead-letter list for permanently-failed background jobs.

Why a Redis list and not a database table: the durable record of a failed
export ALREADY exists in Postgres -- `ExportJob.status = failed` plus
`error_message`, committed by the task itself. This list is an *operational*
convenience on top of that: a single ordered place to see what recently gave
up, and a payload complete enough to replay by hand, without writing a
migration and a second source of truth for the same failure.

That is a deliberate contrast with the sibling CertiFake project's DLQ,
which is Kafka-topic based and MUST succeed before the source offset is
committed -- because there, the DLQ envelope is the only place the original
event and error survive at all (its analysis row has no error column and no
migration tooling to add one safely). Here the row is authoritative, so the
opposite failure bias is correct:

**This fails OPEN.** If Redis is unreachable, the dead-letter push is logged
at CRITICAL and dropped; the worker does not crash and the job is still
correctly marked failed in Postgres with its error message. Losing the
convenience copy must never cost the authoritative one, and must never turn
a finished failure into a crashed worker. Same philosophy as
app/utils/cache.py and app/utils/rate_limit.py: a Redis outage degrades this
app, it does not take it down.

Retention: the list is capped with LTRIM so an unreplayed backlog cannot
grow without bound in a long-running deployment. Capped means old entries
are evicted -- acceptable precisely because Postgres still holds every
failed job permanently.
"""
import json
from datetime import datetime, timezone

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("dead_letter")

DEAD_LETTER_KEY = "devtrack:dead_letter:export_jobs"
MAX_DEAD_LETTERS = 500

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


def push_dead_letter(payload: dict) -> bool:
    """Record an exhausted job. Returns True if stored, False if Redis was unavailable.

    Never raises -- see module docstring for why failing open is the correct
    bias here and not a shortcut.
    """
    entry = {
        **payload,
        "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client = _get_client()
        pipe = client.pipeline()
        pipe.lpush(DEAD_LETTER_KEY, json.dumps(entry, default=str))
        pipe.ltrim(DEAD_LETTER_KEY, 0, MAX_DEAD_LETTERS - 1)
        pipe.execute()
        return True
    except (redis.exceptions.RedisError, ConnectionError, OSError, TypeError, ValueError) as exc:
        # CRITICAL, not warning: a dropped dead letter means an exhausted job
        # has no replay-ready record. The Postgres row still has the failure
        # and its error_message, so this is a lost convenience, not lost data.
        logger.critical(
            "Could not dead-letter job payload to Redis (%s: %s). The job is still marked failed "
            "in the database with its error message; only the replay-ready copy was lost.",
            type(exc).__name__, exc,
        )
        return False


def peek_dead_letters(limit: int = 50) -> list[dict]:
    """Most-recent-first dead letters, for inspection. Empty list if Redis is down."""
    try:
        raw = _get_client().lrange(DEAD_LETTER_KEY, 0, max(limit - 1, 0))
    except (redis.exceptions.RedisError, ConnectionError, OSError) as exc:
        logger.warning("Redis unavailable while reading dead letters: %s", exc)
        return []
    out = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except (TypeError, ValueError):
            out.append({"unparseable_entry": str(item)[:500]})
    return out


def dead_letter_count() -> int:
    """Number of queued dead letters, or -1 if Redis is unreachable.

    -1 rather than 0 so "Redis is down" is distinguishable from "nothing
    failed" -- collapsing those would hide an outage behind a healthy-looking
    zero.
    """
    try:
        return int(_get_client().llen(DEAD_LETTER_KEY))
    except (redis.exceptions.RedisError, ConnectionError, OSError, TypeError, ValueError) as exc:
        logger.warning("Redis unavailable while counting dead letters: %s", exc)
        return -1
