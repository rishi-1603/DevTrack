"""Day-5 deep pass: validation, empty states, id boundaries, and the token matrix.

The 34-operation sweep on Day 1 already proved the happy paths. This file goes
after the inputs a client sends by accident or on purpose: malformed JSON, wrong
types, ids that never existed, empty collections, an expired token, a refresh
token presented as an access token — and the two delete paths that return 500
today. Those two are pinned with `xfail(strict=True)`, so the defect lives in the
suite as well as in the register: fix the FK policy (backlog A1) without removing
the marker and the suite fails, loudly, on purpose.
"""
import time

import jwt
import pytest
from sqlalchemy import text

from app.core.config import settings
from app.database.models import User, UserRole
from app.tests.conftest import TestingSessionLocal, auth_headers, engine


@pytest.fixture(autouse=True)
def _enforce_foreign_keys():
    """Turn SQLite's foreign-key enforcement ON for these tests.

    WHY: SQLite ships with `PRAGMA foreign_keys = 0`, and the shared test engine
    never changes that. Real Postgres does enforce the constraints, so the test
    suite and the production database disagree about what a DELETE does — which
    is precisely how the two delete defects (D1-2, D3-1) survived a green suite
    and were only found by running the app against real Postgres on Days 1 and 3.
    The engine is a StaticPool, so one PRAGMA covers every session in the test.
    """
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    yield


def _promote_to_admin(email: str) -> None:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        user.role = UserRole.ADMIN
        db.add(user)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def project_and_issue(client):
    """Owner with one project and one issue assigned to a second user, i.e. the
    data state in which the delete defect appears."""
    owner = auth_headers(client, email="boundary-owner@example.com")
    project = client.post("/projects", json={"title": "Boundary project", "description": "d"},
                          headers=owner).json()
    issue = client.post("/issues", json={"project_id": project["id"], "title": "Boundary issue",
                                         "priority": "medium"}, headers=owner).json()
    assignee = auth_headers(client, email="boundary-assignee@example.com")
    me = client.get("/users/me", headers=assignee).json()
    client.post(f"/issues/{issue['id']}/assign", json={"user_id": me["id"]}, headers=owner)
    myself = client.get("/users/me", headers=owner).json()
    return {"owner_headers": owner, "project_id": project["id"], "issue_id": issue["id"],
            "owner_id": myself["id"], "assignee_id": me["id"]}


# --------------------------------------------------------------------------
# token matrix
# --------------------------------------------------------------------------

def test_missing_token_is_401(client):
    assert client.get("/projects").status_code == 401


def test_garbage_token_is_401(client):
    assert client.get("/projects", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_token_signed_with_another_key_is_401(client):
    forged = jwt.encode({"sub": "1", "type": "access", "exp": int(time.time()) + 600},
                        "some-other-secret-key-entirely", algorithm="HS256")
    assert client.get("/projects", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_expired_access_token_is_401(client):
    """Signed with the real key, but past its expiry: the signature must not be
    the only thing that matters."""
    headers = auth_headers(client, email="expiry@example.com")
    token = headers["Authorization"].split()[1]
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    payload["exp"] = int(time.time()) - 10
    expired = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    assert client.get("/projects", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_refresh_token_is_not_accepted_as_an_access_token(client):
    auth_headers(client, email="typecheck@example.com")
    login = client.post("/auth/login", data={"username": "typecheck@example.com", "password": "password123"})
    refresh = login.json()["refresh_token"]
    assert client.get("/projects", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401


def test_access_token_is_not_accepted_for_refresh(client):
    headers = auth_headers(client, email="refreshcheck@example.com")
    access = headers["Authorization"].split()[1]
    assert client.post("/auth/refresh", json={"refresh_token": access}).status_code == 401


def test_valid_token_passes_auth(client):
    """Control: the same route with a good token answers 200, so the 401s above
    are auth decisions rather than routing accidents."""
    assert client.get("/projects", headers=auth_headers(client, email="control@example.com")).status_code == 200


# --------------------------------------------------------------------------
# malformed input and wrong types
# --------------------------------------------------------------------------

def test_malformed_json_body_is_422(client):
    headers = {**auth_headers(client, email="malformed@example.com"), "Content-Type": "application/json"}
    resp = client.post("/projects", headers=headers, content=b'{"title": "broken", ')
    assert resp.status_code == 422


def test_wrong_field_types_are_422(client):
    resp = client.post("/projects", headers=auth_headers(client, email="types@example.com"),
                       json={"title": {"not": "a string"}, "description": 42})
    assert resp.status_code == 422


def test_unknown_fields_are_ignored_not_rejected(client):
    """Pydantic's default ignores unknown keys. Asserting it means a future
    `extra="forbid"` is a deliberate change, not an accident that breaks clients."""
    resp = client.post("/projects", headers=auth_headers(client, email="extra@example.com"),
                       json={"title": "Extra fields", "description": "d", "unexpected": True})
    assert resp.status_code == 201


def test_invalid_enum_value_is_422(client, project_and_issue):
    # status is changed through PATCH /issues/{id}/status, not PUT: an unknown
    # `status` key in a PUT body is ignored by the schema, which is itself worth
    # knowing (asserted below).
    ignored = client.put(f"/issues/{project_and_issue['issue_id']}",
                         headers=project_and_issue["owner_headers"], json={"status": "not-a-status"})
    assert ignored.status_code == 200, "PUT ignores unknown fields including status"
    resp = client.patch(f"/issues/{project_and_issue['issue_id']}/status",
                        headers=project_and_issue["owner_headers"], json={"status": "not-a-status"})
    assert resp.status_code == 422


def test_blank_title_is_rejected(client):
    """A project with an empty title is not a project. Whatever the schema's
    minimum is, '' must not pass it."""
    resp = client.post("/projects", headers=auth_headers(client, email="blank@example.com"),
                       json={"title": "", "description": "d"})
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# ids that do not exist
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/projects/999999"),
    ("put", "/projects/999999"),
    ("delete", "/projects/999999"),
    ("get", "/issues/999999"),
    ("delete", "/issues/999999"),
    ("get", "/issues/999999/comments"),
])
def test_unknown_ids_are_404_not_500(client, method, path):
    headers = auth_headers(client, email="unknown-ids@example.com")
    kwargs = {"json": {"title": "x"}} if method == "put" else {}
    resp = getattr(client, method)(path, headers=headers, **kwargs)
    assert resp.status_code == 404, f"{method.upper()} {path} -> {resp.status_code}"


# --------------------------------------------------------------------------
# empty states
# --------------------------------------------------------------------------

def test_a_new_user_sees_empty_collections_not_errors(client):
    headers = auth_headers(client, email="fresh-user@example.com")
    projects = client.get("/projects", headers=headers).json()
    assert projects == {"total": 0, "items": []}, f"/projects envelope changed: {projects}"
    issues = client.get("/issues", headers=headers).json()
    assert issues["total"] == 0 and issues["items"] == []
    notifications = client.get("/notifications", headers=headers).json()
    assert notifications["items"] == [] and notifications["unread_count"] == 0


def test_dashboard_of_a_user_with_no_projects_is_all_zeroes(client):
    body = client.get("/dashboard/summary", headers=auth_headers(client, email="empty-dash@example.com")).json()
    assert body["total_projects"] == 0
    assert body["total_issues"] == 0


# --------------------------------------------------------------------------
# duplicates and repeats
# --------------------------------------------------------------------------

def test_the_same_project_title_can_be_used_twice(client):
    """No uniqueness constraint on a title, and that is intended: two projects
    called 'Q3' is the user's business, not a 409."""
    headers = auth_headers(client, email="duplicate-title@example.com")
    first = client.post("/projects", headers=headers, json={"title": "Duplicate", "description": "a"})
    second = client.post("/projects", headers=headers, json={"title": "Duplicate", "description": "b"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_commenting_twice_creates_two_comments(client, project_and_issue):
    """Nothing here is de-duplicated, so 'idempotent by accident' is not a claim
    anyone can make about this endpoint."""
    for _ in range(2):
        resp = client.post(f"/issues/{project_and_issue['issue_id']}/comments",
                           headers=project_and_issue["owner_headers"], json={"comment": "same text"})
        assert resp.status_code == 201
    listed = client.get(f"/issues/{project_and_issue['issue_id']}/comments",
                        headers=project_and_issue["owner_headers"]).json()
    assert len(listed) == 2


# --------------------------------------------------------------------------
# the documented absence of pagination (D1-10 / decision C2)
# --------------------------------------------------------------------------

def test_list_endpoints_ignore_limit_and_offset(client):
    """There is no pagination today and unknown query parameters are ignored.
    Recording the current contract means that adding limit/offset (decision C2)
    fails this test rather than leaving two behaviours in the codebase."""
    headers = auth_headers(client, email="pagination-probe@example.com")
    for _ in range(3):
        client.post("/projects", headers=headers, json={"title": "page probe", "description": "d"})
    unlimited = client.get("/projects", headers=headers).json()
    limited = client.get("/projects?limit=1&offset=1", headers=headers).json()
    assert unlimited["total"] == limited["total"] == 3, "limit/offset are not implemented yet"
    assert len(limited["items"]) == 3


# --------------------------------------------------------------------------
# defects found by the audit, pinned while they are open
# --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="D1-2: nothing cascades notifications.issue_id / export_jobs.project_id")
def test_deleting_a_project_whose_issue_has_a_notification_succeeds(client, project_and_issue):
    """Currently 500 (ForeignKeyViolation on notifications.issue_id). Expected:
    204, with the notification removed along with the issue it describes."""
    resp = client.delete(f"/projects/{project_and_issue['project_id']}",
                         headers=project_and_issue["owner_headers"])
    assert resp.status_code == 204, f"project delete with a notification attached -> {resp.status_code}"


@pytest.mark.xfail(strict=True, reason="D3-1: DELETE /users/{id} reaches the same missing cascade")
def test_admin_can_delete_a_user_who_owns_work_with_notifications(client, project_and_issue):
    """Offboarding is the operation most likely to run against real data, and it
    is the one that 500s today."""
    auth_headers(client, email="offboarding-admin@example.com")
    _promote_to_admin("offboarding-admin@example.com")
    admin = client.post("/auth/login", data={"username": "offboarding-admin@example.com",
                                            "password": "password123"}).json()
    resp = client.delete(f"/users/{project_and_issue['owner_id']}",
                         headers={"Authorization": f"Bearer {admin['access_token']}"})
    assert resp.status_code == 204, f"admin user delete -> {resp.status_code}"
