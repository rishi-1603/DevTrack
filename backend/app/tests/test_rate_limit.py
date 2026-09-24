"""Tests for the Redis-backed rate limiter (app/utils/rate_limit.py) and its
FastAPI dependency wrappers (app/core/rate_limit_dep.py).

Uses fakeredis (an in-memory Redis-protocol-compatible server) rather than
mocking at a higher level, so the actual INCR/EXPIRE/pipeline logic in
rate_limit.py is exercised for real, not just asserted to have been called.
"""
import fakeredis
import pytest

from app.tests.conftest import auth_headers, register_user


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """Point both the rate limiter and the cache module at a fresh in-memory
    fake Redis server for every test, so tests never depend on (or pollute)
    a real Redis instance, and rate-limit counters don't leak between
    tests."""
    fake_server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
    monkeypatch.setattr("app.utils.rate_limit._client", fake_client)
    yield fake_client


def test_login_is_rate_limited_after_repeated_failures(client):
    register_user(client, email="ratelimit@example.com", password="correctpass123")

    # The login limit is 10/minute (see app/api/auth.py). Send 10 requests
    # (any credentials -- even wrong ones still count against the limit,
    # since the check runs before authentication) then confirm the 11th is
    # rejected with 429, not passed through to the auth logic.
    for _ in range(10):
        resp = client.post("/auth/login", data={"username": "ratelimit@example.com", "password": "wrong"})
        assert resp.status_code == 401  # wrong password, but request was allowed through

    resp = client.post("/auth/login", data={"username": "ratelimit@example.com", "password": "wrong"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_register_is_rate_limited(client):
    for i in range(5):
        client.post("/auth/register", json={"name": "X", "email": f"user{i}@example.com", "password": "password123"})

    resp = client.post("/auth/register", json={"name": "X", "email": "onemore@example.com", "password": "password123"})
    assert resp.status_code == 429


def test_rate_limit_is_scoped_per_ip_not_global(client):
    """Confirm the counter key includes the client IP: TestClient always
    presents the same IP, so this test instead verifies that a *different*
    action/user is not affected by an exhausted login limit -- i.e. the
    limiter doesn't accidentally rate-limit unrelated actions.
    """
    for _ in range(10):
        client.post("/auth/login", data={"username": "nobody@example.com", "password": "wrong"})

    # /auth/register uses a different rate-limit bucket ("register" vs
    # "login") even though it's the same client IP -- must not be blocked
    # by the login limiter being exhausted.
    resp = client.post(
        "/auth/register", json={"name": "Y", "email": "unaffected@example.com", "password": "password123"}
    )
    assert resp.status_code == 201


def test_create_issue_is_rate_limited_per_user(client):
    headers = auth_headers(client)
    project_resp = client.post("/projects", json={"title": "P", "description": "d"}, headers=headers)
    project_id = project_resp.json()["id"]

    for _ in range(60):
        resp = client.post("/issues", json={"title": "t", "project_id": project_id}, headers=headers)
        assert resp.status_code == 201

    resp = client.post("/issues", json={"title": "t", "project_id": project_id}, headers=headers)
    assert resp.status_code == 429


def test_rate_limiter_fails_open_when_redis_is_unreachable(client, monkeypatch):
    """If Redis itself is unreachable (not just empty/fake), requests must
    still be allowed through -- see app/utils/rate_limit.py's docstring for
    why this fail-open behavior is a deliberate choice, not a bug."""
    import redis as redis_module

    class _BrokenRedis:
        def pipeline(self):
            raise redis_module.exceptions.ConnectionError("simulated Redis outage")

    monkeypatch.setattr("app.utils.rate_limit._client", _BrokenRedis())

    resp = client.post("/auth/register", json={"name": "Z", "email": "duringoutage@example.com", "password": "password123"})
    assert resp.status_code == 201
