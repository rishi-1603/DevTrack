"""Shared pytest fixtures: in-memory SQLite DB and a TestClient with overridden dependencies."""
import os

# Must be set before anything under app/ is imported: app/core/config.py
# requires SECRET_KEY with no default (Day 3 fix -- see that file for why).
# setdefault so a real CI/local env value, if one happens to be exported,
# is not clobbered.
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.celery_app import celery_app
from app.database import models  # noqa: F401  (register models on metadata)
from app.database.session import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _enforce_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores FOREIGN KEY constraints unless asked not to.

    WHY THIS EXISTS (Day 5 of the final audit): the suite runs on SQLite, the
    app runs on Postgres, and only one of them was enforcing the constraints.
    That difference is why two real defects -- DELETE /projects/{id} and
    DELETE /users/{id} returning 500 on rows that notifications still reference
    (D1-2, D3-1) -- kept passing a green test suite and were found only by
    running the API against real Postgres. Turning the pragma on makes the test
    database refuse the same statements the real one refuses.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Tests never require a running Redis/Celery broker: task_always_eager makes
# `.delay()` execute the task function synchronously, in-process, instead of
# publishing to a broker. task_eager_propagates re-raises task exceptions
# in the calling test instead of silently swallowing them, so a bug in a
# task fails the test that triggered it, not silently.
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


@pytest.fixture(scope="function", autouse=True)
def _isolate_redis(monkeypatch):
    """Give every test its own empty Redis, so the suite cannot see a real one.

    WHY THIS EXISTS (found on Day 1 of the final audit, by running the suite
    instead of trusting CI): `app/utils/rate_limit.py` and `app/utils/cache.py`
    each build a real `redis.Redis` lazily from `REDIS_HOST`/`REDIS_PORT`, which
    default to localhost:6379. With nothing listening, both fail open and the
    suite passes -- which is exactly what CI does, because the `test` job starts
    no Redis. But this README's own quick start (`docker compose up`) publishes
    Redis on 6379. A developer who follows it and then runs pytest gets a suite
    that talks to a REAL Redis, and two things break:

      * Every test shares one rate-limit bucket keyed on the TestClient IP, so
        partway through the run `/auth/register` starts returning 429 and 50
        tests fail with a downstream `KeyError: 'access_token'` that says
        nothing about the cause. The next run is worse (54 failures), because the
        keys persist between runs until their window expires.
      * The dashboard cache reads and writes the same real Redis, so a summary
        cached by an earlier run can be served to a later one -- a test passing
        on stale ambient data.

    So the suite's outcome depended on infrastructure that happens to be running
    nearby. It no longer does: each test gets a fresh fakeredis instance, which
    keeps the limiter's and the cache's real logic under test (increments, fixed
    windows, TTLs and expiry all execute) while removing the leak. Tests that
    deliberately exercise Redis behaviour install their own client afterwards --
    `test_rate_limit.py` monkeypatches `_client` with its own fakeredis and with
    a broken client for the fail-open case -- and still take precedence, because
    a test-requested fixture runs after this autouse one.
    """
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    monkeypatch.setattr("app.utils.rate_limit._client", client)
    monkeypatch.setattr("app.utils.cache._client", client)
    # Dead letters use the same isolation. Without this the dead-letter tests
    # wrote to the real Redis on localhost, and entries from one run leaked
    # into the next (found Day 7, when a stale entry broke a TTL assertion).
    monkeypatch.setattr("app.utils.dead_letter._client", client)
    yield client


@pytest.fixture(scope="function", autouse=True)
def _reset_db():
    """Create a fresh schema before each test and drop it afterwards."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture()
def client() -> TestClient:
    """A TestClient wired to the isolated in-memory SQLite database."""
    return TestClient(app)


def register_user(client: TestClient, name="Alice Dev", email="alice@example.com", password="password123"):
    response = client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    return response


def login_user(client: TestClient, email="alice@example.com", password="password123"):
    response = client.post(
        "/auth/login",
        data={"username": email, "password": password},
    )
    return response


def auth_headers(client: TestClient, email="alice@example.com", password="password123") -> dict:
    """Register, log in, and return the Authorization header.

    Fails with the CAUSE rather than the symptom. This used to be
    `login_response.json()["access_token"]`, which raised a bare
    `KeyError: 'access_token'` in 50 tests when registration had been rate
    limited -- an error that pointed at the wrong function in the wrong file.
    """
    register_response = register_user(client, email=email, password=password)
    login_response = login_user(client, email=email, password=password)
    body = login_response.json() if login_response.content else {}
    if "access_token" not in body:
        raise AssertionError(
            f"login returned HTTP {login_response.status_code} with no access_token "
            f"({login_response.text[:160]}); registration returned HTTP "
            f"{register_response.status_code} ({register_response.text[:160]}). "
            "A 429 from either means a rate-limit window is being shared between "
            "tests -- see the _isolate_redis fixture above."
        )
    return {"Authorization": f"Bearer {body['access_token']}"}
