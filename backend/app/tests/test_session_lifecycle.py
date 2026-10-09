"""Database session lifecycle around failing requests (Day 7).

The conftest `_override_get_db` dependency opens one session per request and
closes it in a `finally`. These tests check that this holds on the paths that
matter: requests that return 4xx, requests that raise, and requests where the
database itself errors.

Note on what each test proves. A test that only checks committed data after a
DB error does NOT prove the dependency's cleanup, because SQLAlchemy resets a
pooled connection on return anyway. The counting test is the one that fails
when the `finally` is removed.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from app.services import project_service
from app.tests import conftest
from app.tests.conftest import auth_headers


@pytest.fixture()
def counting_sessions(monkeypatch):
    """Replace the test session factory with one that counts opens and closes."""
    stats = {"opened": 0, "closed": 0}

    class CountingSession(Session):
        def close(self):
            stats["closed"] += 1
            return super().close()

    def factory():
        stats["opened"] += 1
        return CountingSession(autocommit=False, autoflush=False, bind=conftest.engine)

    monkeypatch.setattr(conftest, "TestingSessionLocal", factory)
    return stats


def test_failed_create_leaves_no_row(client):
    headers = auth_headers(client)
    r = client.post("/issues", json={"title": "orphan", "project_id": 999}, headers=headers)
    assert r.status_code == 404

    db = conftest.TestingSessionLocal()
    try:
        assert db.execute(text("SELECT COUNT(*) FROM issues")).scalar() == 0
    finally:
        db.close()


def test_normal_requests_still_work_after_several_404s(client):
    headers = auth_headers(client)
    for _ in range(3):
        assert client.get("/projects/999", headers=headers).status_code == 404
    r = client.post("/projects", json={"title": "after the errors", "description": "ok"}, headers=headers)
    assert r.status_code == 201
    assert client.get("/projects", headers=headers).json()["total"] == 1


def test_every_request_closes_its_session_even_when_it_fails(client, counting_sessions, monkeypatch):
    """Counts sessions opened and closed across ok, 404 and raising requests.

    This is the test that fails when `_override_get_db` loses its `finally`.
    A count of successful requests alone would pass without it.
    """
    headers = auth_headers(client)
    # auth_headers itself makes requests; measure from here on.
    counting_sessions["opened"] = counting_sessions["closed"] = 0

    assert client.get("/projects", headers=headers).status_code == 200
    assert client.get("/projects/999", headers=headers).status_code == 404

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced failure inside the request")

    monkeypatch.setattr(project_service, "list_projects", _boom)
    failing = TestClient(app, raise_server_exceptions=False)
    assert failing.get("/projects", headers=headers).status_code == 500

    assert counting_sessions["opened"] >= 3
    assert counting_sessions["opened"] == counting_sessions["closed"], (
        f"opened {counting_sessions['opened']} sessions but closed {counting_sessions['closed']}"
    )


def test_database_error_mid_request_does_not_poison_later_requests(client, monkeypatch):
    """A real DBAPI error inside a request yields a 500 and leaves no partial data.

    The failing statement is real SQL (a missing column), not a mocked exception.
    Later requests must still work and must see no partial row.
    """
    headers = auth_headers(client)

    def _create_then_fail(db, payload, user):
        db.execute(text("INSERT INTO projects (title, description, owner_id) VALUES ('partial', 'x', :uid)"),
                   {"uid": user.id})
        db.execute(text("SELECT this_column_does_not_exist FROM projects"))
        raise AssertionError("unreachable")

    monkeypatch.setattr(project_service, "create_project", _create_then_fail)
    failing = TestClient(app, raise_server_exceptions=False)
    r = failing.post("/projects", json={"title": "doomed", "description": "x"}, headers=headers)
    assert r.status_code == 500

    monkeypatch.undo()
    listed = client.get("/projects", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 0, "the partial INSERT from the failed request was committed"
    assert client.post("/projects", json={"title": "fine", "description": "x"}, headers=headers).status_code == 201
