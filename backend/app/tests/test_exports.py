"""Tests for the async CSV export feature (Celery task + API endpoints).

Runs the Celery task synchronously in-process (task_always_eager, set in
conftest.py) against the same in-memory SQLite database the test API client
uses, by patching app.tasks.export_tasks.SessionLocal -- the task normally
opens its own session because it runs in a separate worker process in
production, but in tests there is no separate process, so it must be
pointed at the same shared in-memory DB the API wrote to.
"""
import pytest

from app.tests.conftest import TestingSessionLocal, auth_headers, register_user


@pytest.fixture(autouse=True)
def _patch_task_session(monkeypatch):
    monkeypatch.setattr("app.tasks.export_tasks.SessionLocal", TestingSessionLocal)


def _create_project(client, headers, title="Sprint 1"):
    resp = client.post("/projects", json={"title": title, "description": "d"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_issue(client, headers, project_id, title="Fix bug"):
    resp = client.post(
        "/issues",
        json={"title": title, "project_id": project_id, "priority": "high"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_export_job_runs_to_completion_and_downloads(client):
    headers = auth_headers(client)
    project_id = _create_project(client, headers)
    _create_issue(client, headers, project_id, "Issue A")
    _create_issue(client, headers, project_id, "Issue B")

    resp = client.post(f"/projects/{project_id}/export", headers=headers)
    assert resp.status_code == 202, resp.text
    job = resp.json()
    job_id = job["id"]
    # task_always_eager means the task already ran synchronously, in the
    # same call, before request_export() returned -- and export_service
    # re-fetches the row after enqueuing specifically so the response
    # reflects that, rather than returning a stale "pending" snapshot.
    assert job["status"] == "completed"

    status_resp = client.get(f"/export-jobs/{job_id}", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "completed"
    assert status_resp.json()["row_count"] == 2

    download_resp = client.get(f"/export-jobs/{job_id}/download", headers=headers)
    assert download_resp.status_code == 200
    assert "Issue A" in download_resp.text
    assert "Issue B" in download_resp.text
    assert download_resp.headers["content-type"].startswith("text/csv")


def test_export_requires_auth(client):
    resp = client.post("/projects/1/export")
    assert resp.status_code == 401


def test_export_of_nonexistent_project_is_404(client):
    headers = auth_headers(client)
    resp = client.post("/projects/999999/export", headers=headers)
    assert resp.status_code == 404


def test_other_user_cannot_view_or_download_someone_elses_export(client):
    owner_headers = auth_headers(client, email="owner@example.com")
    project_id = _create_project(client, owner_headers)
    _create_issue(client, owner_headers, project_id)

    resp = client.post(f"/projects/{project_id}/export", headers=owner_headers)
    job_id = resp.json()["id"]

    register_user(client, email="stranger@example.com")
    stranger_headers = auth_headers(client, email="stranger@example.com")

    status_resp = client.get(f"/export-jobs/{job_id}", headers=stranger_headers)
    assert status_resp.status_code == 403

    download_resp = client.get(f"/export-jobs/{job_id}/download", headers=stranger_headers)
    assert download_resp.status_code == 403


def test_downloading_before_completion_is_rejected(client, monkeypatch):
    """If a job is still pending/running (or failed), downloading it must be
    rejected rather than serving a partial/nonexistent file."""
    from app.database.models import ExportJob, ExportJobStatus

    headers = auth_headers(client)
    project_id = _create_project(client, headers)
    user_id = client.get("/users/me", headers=headers).json()["id"]

    db = TestingSessionLocal()
    try:
        job = ExportJob(project_id=project_id, requested_by_id=user_id, status=ExportJobStatus.PENDING)
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    finally:
        db.close()

    resp = client.get(f"/export-jobs/{job_id}/download", headers=headers)
    assert resp.status_code == 400
