"""Shared pytest fixtures: in-memory SQLite DB and a TestClient with overridden dependencies."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
