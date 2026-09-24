"""Shared pytest fixtures: in-memory SQLite DB and a TestClient with overridden dependencies."""
import os

# Must be set before anything under app/ is imported: app/core/config.py
# requires SECRET_KEY with no default (Day 3 fix -- see that file for why).
# setdefault so a real CI/local env value, if one happens to be exported,
# is not clobbered.
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Tests never require a running Redis/Celery broker: task_always_eager makes
# `.delay()` execute the task function synchronously, in-process, instead of
# publishing to a broker. task_eager_propagates re-raises task exceptions
# in the calling test instead of silently swallowing them, so a bug in a
# task fails the test that triggered it, not silently.
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


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
    register_user(client, email=email, password=password)
    login_response = login_user(client, email=email, password=password)
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
