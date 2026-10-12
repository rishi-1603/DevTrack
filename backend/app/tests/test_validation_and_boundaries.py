"""Day-5 deep pass: validation, empty states, id boundaries, and the token matrix.

The 34-operation sweep on Day 1 already proved the happy paths. This file goes
after the inputs a client sends by accident or on purpose: malformed JSON, wrong
types, ids that never existed, empty collections, an expired token, a refresh
token presented as an access token — and the delete paths that used to return
500 when rows (notifications, then export jobs) still referenced the project or
user being deleted.

Those defects (D1-2, D3-1) are fixed, and the `xfail(strict=True)` markers that
pinned them are gone on purpose: the same tests now assert a real 204, so the
suite fails if the cascades are ever removed again. Both halves are covered —
notifications, and the `export_jobs` rows the original fix left untested.
"""
import time

import jwt
import pytest
from sqlalchemy import text

from app.core.config import settings
from app.database.models import ExportJob, User, UserRole
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
# delete cascades (D1-2, D3-1): fixed -- these are regression tests now
# --------------------------------------------------------------------------

def test_deleting_a_project_whose_issue_has_a_notification_succeeds(client, project_and_issue):
    """Currently 500 (ForeignKeyViolation on notifications.issue_id). Expected:
    204, with the notification removed along with the issue it describes."""
    resp = client.delete(f"/projects/{project_and_issue['project_id']}",
                         headers=project_and_issue["owner_headers"])
    assert resp.status_code == 204, f"project delete with a notification attached -> {resp.status_code}"


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


# --------------------------------------------------------------------------
# D1-2 / D3-1, second half: export_jobs. The two tests above cover the
# notifications table. They do NOT cover `export_jobs.project_id` or
# `export_jobs.requested_by_id`, which the same fix claims to cascade -- and
# that gap is not hypothetical: the fix as first written declared
# `ExportJob.project` / `ExportJob.requested_by` twice in the class body, and
# the later, plain `relationship("Project")` / `relationship("User")` versions
# shadowed the back_populates ones, so the mapper resolved a configuration the
# author did not intend. Verified by inspecting
# `ExportJob.__mapper__.relationships["project"].back_populates` (None before
# the de-duplication, "export_jobs" after). These two tests are what make that
# visible from the suite rather than only from a mapper probe.
# --------------------------------------------------------------------------

def _attach_export_job(project_id: int, user_id: int) -> int:
    """Insert an export_jobs row directly and return its id.

    Deliberately not via `POST /projects/{id}/export`: that path runs the Celery
    task, which needs its session factory patched (see
    test_export_reliability.py's `_patch_task_session`) and writes a CSV. None of
    that is under test here -- what is under test is what a DELETE does to an
    existing `export_jobs` row, so the row is created directly.
    """
    db = TestingSessionLocal()
    try:
        job = ExportJob(project_id=project_id, requested_by_id=user_id)
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id
    finally:
        db.close()


def test_deleting_a_project_that_has_an_export_job_succeeds(client, project_and_issue):
    """D1-2's export half: `export_jobs.project_id` has no cascade, so deleting
    a project that was ever exported must not 500.

    Expected: 204, and the job row goes with the project. If the cascade is
    missing the ORM tries to null out `project_id` (NOT NULL) and the request
    fails as an IntegrityError -> 500, which is what this asserts against.
    """
    job_id = _attach_export_job(project_and_issue["project_id"], project_and_issue["owner_id"])

    resp = client.delete(f"/projects/{project_and_issue['project_id']}",
                         headers=project_and_issue["owner_headers"])
    assert resp.status_code == 204, f"project delete with an export job attached -> {resp.status_code}"

    db = TestingSessionLocal()
    try:
        assert db.get(ExportJob, job_id) is None, "export job outlived its project"
    finally:
        db.close()


def test_admin_can_delete_a_user_who_requested_an_export(client, project_and_issue):
    """D3-1's export half: `export_jobs.requested_by_id` has no cascade, so
    deleting a user who ever exported a project must not 500.

    This is the harder direction: the delete has to unwind user -> projects ->
    issues -> notifications *and* user -> export_jobs in one transaction.
    """
    job_id = _attach_export_job(project_and_issue["project_id"], project_and_issue["owner_id"])

    auth_headers(client, email="export-offboarding-admin@example.com")
    _promote_to_admin("export-offboarding-admin@example.com")
    admin = client.post("/auth/login", data={"username": "export-offboarding-admin@example.com",
                                            "password": "password123"}).json()
    resp = client.delete(f"/users/{project_and_issue['owner_id']}",
                         headers={"Authorization": f"Bearer {admin['access_token']}"})
    assert resp.status_code == 204, f"admin user delete with export job -> {resp.status_code}"

    db = TestingSessionLocal()
    try:
        assert db.get(ExportJob, job_id) is None, "export job outlived its requester"
    finally:
        db.close()

