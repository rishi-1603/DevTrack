"""Query-count and index tests for the list endpoints (Day 7).

WHY THESE EXIST: the first Day-7 measurement found the issue list serialising
`assignee` lazily, so a page of N issues cost N+1 SELECTs. The fix is a
`selectinload` in `issue_service.list_issues`. These tests keep that fix honest
by counting the statements a request actually issues: if a list endpoint starts
doing per-row queries again, the count grows with the number of rows and the
test fails. Counting is done against the same engine the app uses in tests.

The index tests use SQLite's EXPLAIN QUERY PLAN. Postgres plans were measured
separately (see docs/db/EXPLAIN_OUTPUT.md); this checks that the SQL the API
issues can use the indexes the migration creates.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text

from app.database.models import Notification, NotificationType
from app.tests import conftest
from app.tests.conftest import auth_headers


@pytest.fixture()
def statement_counter():
    """Count SQL statements executed on the test engine while a block runs."""
    counter = {"n": 0, "statements": []}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1
        counter["statements"].append(statement)

    event.listen(conftest.engine, "before_cursor_execute", _count)

    class _Counter:
        def reset(self):
            counter["n"] = 0
            counter["statements"] = []

        @property
        def count(self):
            return counter["n"]

        @property
        def statements(self):
            return counter["statements"]

    yield _Counter()
    event.remove(conftest.engine, "before_cursor_execute", _count)


def _seed_projects(client, headers, n):
    ids = []
    for i in range(n):
        r = client.post("/projects", json={"title": f"Project {i}", "description": "seed"}, headers=headers)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def _seed_issues(client, headers, project_id, n, assignee_id=None):
    ids = []
    for i in range(n):
        body = {"title": f"Issue {i}", "project_id": project_id, "priority": "high"}
        if assignee_id is not None:
            body["assigned_to"] = assignee_id
        r = client.post("/issues", json=body, headers=headers)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def _user_id(client, headers):
    return client.get("/users/me", headers=headers).json()["id"]


# --- N+1 guards on list endpoints -------------------------------------------

def test_list_projects_statement_count_does_not_grow_with_rows(client, statement_counter):
    headers = auth_headers(client)
    _seed_projects(client, headers, 1)
    statement_counter.reset()
    assert client.get("/projects", headers=headers).status_code == 200
    with_one = statement_counter.count

    _seed_projects(client, headers, 6)
    statement_counter.reset()
    assert client.get("/projects", headers=headers).status_code == 200
    with_seven = statement_counter.count

    assert with_seven == with_one, f"list_projects issued {with_one} statements for 1 row but {with_seven} for 7"


def test_list_issues_statement_count_does_not_grow_with_rows(client, statement_counter):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    _seed_issues(client, headers, pid, 1)
    statement_counter.reset()
    assert client.get(f"/issues?project_id={pid}", headers=headers).status_code == 200
    with_one = statement_counter.count

    _seed_issues(client, headers, pid, 7)
    statement_counter.reset()
    assert client.get(f"/issues?project_id={pid}", headers=headers).status_code == 200
    with_eight = statement_counter.count

    assert with_eight == with_one, f"list_issues issued {with_one} statements for 1 row but {with_eight} for 8"


def test_list_issues_with_assignees_does_not_issue_one_query_per_assignee(client, statement_counter):
    """`assignee` is a relationship serialised into every issue. selectinload batches it."""
    headers = auth_headers(client)
    uid = _user_id(client, headers)
    [pid] = _seed_projects(client, headers, 1)
    _seed_issues(client, headers, pid, 1, assignee_id=uid)
    statement_counter.reset()
    client.get(f"/issues?project_id={pid}", headers=headers)
    with_one = statement_counter.count

    _seed_issues(client, headers, pid, 9, assignee_id=uid)
    statement_counter.reset()
    body = client.get(f"/issues?project_id={pid}", headers=headers).json()
    assert body["total"] == 10
    assert statement_counter.count == with_one, "assignee lookup grew with the number of issues: N+1 is back"


def test_list_comments_statement_count_does_not_grow_with_rows(client, statement_counter):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    [iid] = _seed_issues(client, headers, pid, 1)
    client.post(f"/issues/{iid}/comments", json={"comment": "first"}, headers=headers)
    statement_counter.reset()
    client.get(f"/issues/{iid}/comments", headers=headers)
    with_one = statement_counter.count

    for i in range(7):
        client.post(f"/issues/{iid}/comments", json={"comment": f"c{i}"}, headers=headers)
    statement_counter.reset()
    listed = client.get(f"/issues/{iid}/comments", headers=headers)
    assert listed.status_code == 200 and len(listed.json()) == 8
    assert statement_counter.count == with_one


def test_list_notifications_statement_count_does_not_grow_with_rows(client, statement_counter):
    headers = auth_headers(client)
    uid = _user_id(client, headers)

    def _add(n):
        db = conftest.TestingSessionLocal()
        try:
            for i in range(n):
                db.add(Notification(user_id=uid, type=NotificationType.ISSUE_ASSIGNED,
                                    message=f"n{i}", created_at=datetime.now(timezone.utc)))
            db.commit()
        finally:
            db.close()

    _add(1)
    statement_counter.reset()
    client.get("/notifications", headers=headers)
    with_one = statement_counter.count

    _add(9)
    statement_counter.reset()
    body = client.get("/notifications", headers=headers).json()
    assert body["total"] == 10
    assert statement_counter.count == with_one


def test_dashboard_summary_statement_count_is_bounded(client, statement_counter):
    """The summary is a fixed set of aggregate queries, not one per project or issue."""
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    _seed_issues(client, headers, pid, 2)
    statement_counter.reset()
    assert client.get("/dashboard/summary", headers=headers).status_code == 200
    small = statement_counter.count

    for p in _seed_projects(client, headers, 5):
        _seed_issues(client, headers, p, 2)
    statement_counter.reset()
    assert client.get("/dashboard/summary", headers=headers).status_code == 200
    large = statement_counter.count

    assert large == small, f"summary issued {small} statements for 2 issues and {large} for 12"


def test_second_dashboard_call_is_served_from_cache(client, statement_counter):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    _seed_issues(client, headers, pid, 3)
    first = client.get("/dashboard/summary", headers=headers)
    statement_counter.reset()
    second = client.get("/dashboard/summary", headers=headers)
    assert first.json() == second.json()
    # Auth still reads the user; the aggregate queries must not run again.
    aggregate = [s for s in statement_counter.statements if "count(" in s.lower() or "sum(" in s.lower()]
    assert aggregate == [], f"cache hit still ran aggregates: {aggregate}"


# --- Index usage (EXPLAIN QUERY PLAN on the SQL the API issues) --------------

def _plan(sql: str) -> str:
    with conftest.engine.connect() as conn:
        rows = conn.execute(text("EXPLAIN QUERY PLAN " + sql)).fetchall()
    return " | ".join(str(r[-1]) for r in rows)


@pytest.mark.parametrize(
    "sql, index",
    [
        ("SELECT * FROM issues WHERE project_id = 1", "ix_issues_project_id"),
        ("SELECT * FROM issues WHERE status = 'todo'", "ix_issues_status"),
        ("SELECT * FROM issues WHERE priority = 'high'", "ix_issues_priority"),
        ("SELECT * FROM comments WHERE issue_id = 1 ORDER BY created_at", "ix_comments_issue_id_created_at"),
        ("SELECT * FROM notifications WHERE user_id = 1 ORDER BY created_at", "ix_notifications_user_id_created_at"),
        ("SELECT * FROM export_jobs WHERE status = 'queued' ORDER BY created_at", "ix_export_jobs_status_created_at"),
    ],
)
def test_filtered_query_uses_an_index(sql, index, _reset_db):
    """`_reset_db` (autouse) has created the schema; this only asks the planner."""
    plan = _plan(sql)
    assert index in plan, f"expected {index} in plan, got: {plan}"


def test_projects_owner_lookup_uses_an_index(_reset_db):
    assert "ix_projects_owner_id" in _plan("SELECT * FROM projects WHERE owner_id = 1")


def test_issue_list_order_is_newest_first(client):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    ids = _seed_issues(client, headers, pid, 3)
    listed = [i["id"] for i in client.get(f"/issues?project_id={pid}", headers=headers).json()["items"]]
    assert listed == list(reversed(ids))


def test_project_search_returns_only_matches(client):
    headers = auth_headers(client)
    client.post("/projects", json={"title": "Payments rewrite", "description": "x"}, headers=headers)
    client.post("/projects", json={"title": "Mobile app", "description": "x"}, headers=headers)
    body = client.get("/projects?search=payments", headers=headers).json()
    assert [p["title"] for p in body["items"]] == ["Payments rewrite"]


def test_issue_search_is_case_insensitive(client):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    _seed_issues(client, headers, pid, 2)
    body = client.get(f"/issues?project_id={pid}&search=ISSUE 1", headers=headers).json()
    assert body["total"] == 1


def test_issue_status_filter_returns_only_that_status(client):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    [iid, _] = _seed_issues(client, headers, pid, 2)
    # The state machine only allows todo -> in_progress -> done; a direct jump is a 400.
    moved = client.patch(f"/issues/{iid}/status", json={"status": "in_progress"}, headers=headers)
    assert moved.status_code == 200, moved.text
    body = client.get(f"/issues?project_id={pid}&status=in_progress", headers=headers).json()
    assert [i["id"] for i in body["items"]] == [iid]


def test_issue_priority_filter_returns_only_that_priority(client):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    _seed_issues(client, headers, pid, 2)
    client.post("/issues", json={"title": "Low one", "project_id": pid, "priority": "low"}, headers=headers)
    body = client.get(f"/issues?project_id={pid}&priority=low", headers=headers).json()
    assert body["total"] == 1 and body["items"][0]["title"] == "Low one"


def test_comments_are_listed_oldest_first(client):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    [iid] = _seed_issues(client, headers, pid, 1)
    for text_ in ("one", "two", "three"):
        client.post(f"/issues/{iid}/comments", json={"comment": text_}, headers=headers)
    listed = [c["comment"] for c in client.get(f"/issues/{iid}/comments", headers=headers).json()]
    assert listed == ["one", "two", "three"]


def test_comments_belong_only_to_their_issue(client):
    headers = auth_headers(client)
    [pid] = _seed_projects(client, headers, 1)
    a, b = _seed_issues(client, headers, pid, 2)
    client.post(f"/issues/{a}/comments", json={"comment": "on a"}, headers=headers)
    assert client.get(f"/issues/{b}/comments", headers=headers).json() == []


def test_notification_count_is_reported_with_unread_total(client):
    headers = auth_headers(client)
    uid = _user_id(client, headers)
    db = conftest.TestingSessionLocal()
    try:
        db.add(Notification(user_id=uid, type=NotificationType.ISSUE_ASSIGNED, message="a",
                            created_at=datetime.now(timezone.utc)))
        db.commit()
    finally:
        db.close()
    body = client.get("/notifications", headers=headers).json()
    assert body["total"] == 1 and body["unread_count"] == 1
