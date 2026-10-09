"""Redis failure and TTL behaviour for the three Redis-backed helpers (Day 7).

The app treats Redis as optional: the dashboard cache, the rate limiter and the
dead-letter list all degrade instead of failing the request. These tests use a
client whose every call raises a connection error, so the fail-open paths run
for real, plus fakeredis (from the autouse `_isolate_redis` fixture) to check
TTLs and invalidation.
"""
import pytest
import redis

from app.tests.conftest import auth_headers
from app.utils import cache, dead_letter, rate_limit


class ExplodingRedis:
    """Stands in for a Redis server that is down: every operation fails."""

    def __getattr__(self, name):
        def _fail(*args, **kwargs):
            raise redis.exceptions.ConnectionError(f"simulated outage on {name}")

        return _fail


@pytest.fixture()
def dead_redis(monkeypatch):
    dead = ExplodingRedis()
    monkeypatch.setattr(cache, "_client", dead)
    monkeypatch.setattr(rate_limit, "_client", dead)
    monkeypatch.setattr(dead_letter, "_client", dead)
    return dead


# --- cache.py ----------------------------------------------------------------

def test_get_cache_is_a_miss_when_redis_is_down(dead_redis):
    assert cache.get_cache("any:key") is None


def test_set_and_delete_cache_do_not_raise_when_redis_is_down(dead_redis):
    cache.set_cache("any:key", {"x": 1}, 60)
    cache.delete_cache("any:key")


def test_set_cache_stores_json_with_a_ttl(_isolate_redis):
    cache.set_cache("dash:1", {"total": 3}, 45)
    assert cache.get_cache("dash:1") == {"total": 3}
    ttl = _isolate_redis.ttl("dash:1")
    assert 0 < ttl <= 45


def test_delete_cache_removes_the_key(_isolate_redis):
    cache.set_cache("dash:2", {"total": 1}, 60)
    cache.delete_cache("dash:2")
    assert cache.get_cache("dash:2") is None


def test_dashboard_summary_still_answers_when_redis_is_down(client, dead_redis):
    headers = auth_headers(client)
    r = client.get("/dashboard/summary", headers=headers)
    assert r.status_code == 200
    assert r.json()["total_issues"] == 0


def test_dashboard_summary_is_cached_with_its_ttl(client, _isolate_redis):
    from app.core.config import settings

    headers = auth_headers(client)
    client.get("/dashboard/summary", headers=headers)
    user_id = client.get("/users/me", headers=headers).json()["id"]
    key = f"dashboard:summary:{user_id}"
    assert _isolate_redis.exists(key) == 1
    assert 0 < _isolate_redis.ttl(key) <= settings.DASHBOARD_CACHE_TTL_SECONDS


def test_invalidate_dashboard_cache_drops_the_key(_isolate_redis):
    from app.services import dashboard_service

    cache.set_cache(dashboard_service._cache_key(7), {"x": 1}, 60)
    dashboard_service.invalidate_dashboard_cache(7)
    assert _isolate_redis.exists(dashboard_service._cache_key(7)) == 0


# --- rate_limit.py -----------------------------------------------------------

def test_rate_limit_fails_open_when_redis_is_down(dead_redis):
    assert rate_limit.is_allowed("login:1.2.3.4", limit=1, window_seconds=60) == (True, 0)


def test_rate_limit_blocks_after_the_limit_and_reports_retry_after(_isolate_redis):
    results = [rate_limit.is_allowed("k", limit=3, window_seconds=60) for _ in range(4)]
    assert [allowed for allowed, _ in results] == [True, True, True, False]
    retry_after = results[-1][1]
    assert 0 < retry_after <= 60


# --- dead_letter.py ----------------------------------------------------------

def test_dead_letter_push_reports_failure_and_does_not_raise_when_redis_is_down(dead_redis):
    assert dead_letter.push_dead_letter({"job_id": 1}) is False


def test_dead_letter_count_is_minus_one_when_redis_is_down(dead_redis):
    """-1 keeps "Redis is down" distinct from "nothing failed"."""
    assert dead_letter.dead_letter_count() == -1
    assert dead_letter.peek_dead_letters() == []
    assert dead_letter.clear_dead_letters() is False


def test_dead_letter_list_has_a_sliding_seven_day_ttl(_isolate_redis):
    dead_letter.push_dead_letter({"job_id": 1})
    ttl = _isolate_redis.ttl(dead_letter.DEAD_LETTER_KEY)
    assert 0 < ttl <= dead_letter.DEAD_LETTER_TTL_SECONDS
    # A later push refreshes the window rather than letting the old one run out.
    _isolate_redis.expire(dead_letter.DEAD_LETTER_KEY, 10)
    dead_letter.push_dead_letter({"job_id": 2})
    assert _isolate_redis.ttl(dead_letter.DEAD_LETTER_KEY) > 10


def test_dead_letter_list_is_capped_and_newest_first(_isolate_redis):
    for i in range(dead_letter.MAX_DEAD_LETTERS + 5):
        dead_letter.push_dead_letter({"job_id": i})
    assert dead_letter.dead_letter_count() == dead_letter.MAX_DEAD_LETTERS
    assert dead_letter.peek_dead_letters(1)[0]["job_id"] == dead_letter.MAX_DEAD_LETTERS + 4


def test_unparseable_dead_letter_entry_is_reported_not_raised(_isolate_redis):
    _isolate_redis.lpush(dead_letter.DEAD_LETTER_KEY, "{not json")
    entries = dead_letter.peek_dead_letters()
    assert "unparseable_entry" in entries[0]
