"""Tests for Day 4 export-job reliability: real retries, dead-lettering, and
recovery of jobs abandoned by a dead worker.

Two things about the Celery eager-mode setup are load-bearing here and were
determined empirically rather than assumed (Celery 5.4):

  - conftest.py sets `task_always_eager=True` so tasks run in-process with no
    broker, and `task_eager_propagates=True` so a task's exceptions reach the
    test. But with eager-propagates ON, `self.retry()` raises a `Retry`
    exception straight to the caller and the task is NOT re-executed -- so a
    test that wants to observe retry behaviour must turn that flag OFF for
    the duration of the test. With it OFF, eager mode genuinely re-runs the
    task (verified: 3 invocations for max_retries=2, terminal result carries
    attempts=3).
  - Backoff `countdown` is ignored under eager mode, so these tests do not
    sleep. The countdown *value* is tested separately and directly.
"""
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

import app.tasks.export_tasks as export_tasks
from app.database.models import ExportJob, ExportJobStatus
from app.tasks.export_tasks import (
    MAX_ATTEMPTS,
    _retry_countdown,
    generate_project_export,
    recover_stranded_export_jobs,
)
from app.tests.conftest import TestingSessionLocal, auth_headers, register_user
from app.utils import dead_letter


@pytest.fixture(autouse=True)
def _patch_task_session(monkeypatch):
    """Point the task's own session factory at the shared in-memory test DB."""
    monkeypatch.setattr(export_tasks, "SessionLocal", TestingSessionLocal)


@pytest.fixture
def fake_dlq_redis(monkeypatch):
    """In-memory Redis for the dead-letter list (same approach as test_rate_limit.py)."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    monkeypatch.setattr(dead_letter, "_client", client)
    yield client


@pytest.fixture
def captured_dlq(monkeypatch):
    """Capture push_dead_letter payloads without needing Redis at all."""
    calls = []
    monkeypatch.setattr(export_tasks, "push_dead_letter", lambda payload: calls.append(payload) or True)
    return calls


@pytest.fixture
def eager_retries():
    """Allow self.retry() to actually re-execute in eager mode (see module docstring)."""
    from app.core.celery_app import celery_app

    previous = celery_app.conf.task_eager_propagates
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_eager_propagates = previous


def _make_job(project_id: int, user_id: int, status=ExportJobStatus.PENDING, created_at=None) -> ExportJob:
    db = TestingSessionLocal()
    try:
        job = ExportJob(project_id=project_id, requested_by_id=user_id, status=status)
        db.add(job)
        db.commit()
        db.refresh(job)
        if created_at is not None:
            # Set after insert: the column has a Python-side default that would
            # otherwise overwrite an explicit past timestamp on flush.
            job.created_at = created_at
            db.add(job)
            db.commit()
            db.refresh(job)
        return job
    finally:
        db.close()


def _setup_project(client, headers):
    register_user(client, email="retry@example.com", password="password123")
    resp = client.post("/projects", json={"title": "P", "description": "d"}, headers=headers)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    client.post(
        "/issues",
        json={"title": "Issue A", "project_id": project_id, "priority": "high"},
        headers=headers,
    )
    user_id = client.get("/users/me", headers=headers).json()["id"]
    return project_id, user_id


def test_transient_failure_retries_then_succeeds(client, eager_retries, captured_dlq, monkeypatch):
    """A blip on attempt 1-2 must NOT permanently fail the job -- this is the
    exact bug the inert `max_retries=2` decorator used to hide."""
    headers = auth_headers(client, email="retry@example.com")
    project_id, user_id = _setup_project(client, headers)
    job = _make_job(project_id, user_id)

    real_path = export_tasks._csv_path
    calls = {"n": 0}

    def flaky(job_id):
        calls["n"] += 1
        if calls["n"] < MAX_ATTEMPTS:
            raise OSError("simulated transient disk blip")
        return real_path(job_id)

    monkeypatch.setattr(export_tasks, "_csv_path", flaky)

    result = generate_project_export.delay(job.id).get()

    assert result["status"] == "completed"
    assert result["attempts"] == MAX_ATTEMPTS
    assert calls["n"] == MAX_ATTEMPTS
    assert captured_dlq == []  # recovered, so nothing dead-lettered

    db = TestingSessionLocal()
    try:
        row = db.get(ExportJob, job.id)
        assert row.status == ExportJobStatus.COMPLETED
        assert row.attempts == MAX_ATTEMPTS
        # A job that recovered must not carry a stale error implying failure.
        assert row.error_message is None
        assert row.row_count == 1
    finally:
        db.close()


def test_exhausted_retries_marks_failed_and_dead_letters(client, eager_retries, captured_dlq, monkeypatch):
    headers = auth_headers(client, email="retry2@example.com")
    project_id, user_id = _setup_project(client, headers)
    job = _make_job(project_id, user_id)

    monkeypatch.setattr(
        export_tasks, "_csv_path",
        lambda job_id: (_ for _ in ()).throw(OSError("disk is genuinely full")),
    )

    result = generate_project_export.delay(job.id).get()

    assert result["status"] == "failed"
    assert result["attempts"] == MAX_ATTEMPTS

    db = TestingSessionLocal()
    try:
        row = db.get(ExportJob, job.id)
        assert row.status == ExportJobStatus.FAILED
        assert row.attempts == MAX_ATTEMPTS
        assert "disk is genuinely full" in row.error_message
        # Terminal, so a client polling stops waiting instead of hanging.
        assert row.completed_at is not None
    finally:
        db.close()

    assert len(captured_dlq) == 1
    entry = captured_dlq[0]
    assert entry["job_id"] == job.id
    assert entry["project_id"] == project_id
    assert entry["error_type"] == "OSError"
    assert entry["attempts"] == MAX_ATTEMPTS
    # Marked replayable: the failure was environmental, not a logic bug.
    assert entry["retryable"] is True


def test_missing_job_row_does_not_burn_retries(client, eager_retries, captured_dlq):
    """Permanent failure: no number of attempts can create a missing row, so
    retrying only delays the terminal state and wastes a worker slot."""
    result = generate_project_export.delay(999999).get()

    assert result["status"] == "failed"
    assert result["reason"] == "job not found"
    assert result["attempts"] == 1  # exactly one attempt, no retries
    assert len(captured_dlq) == 1
    assert captured_dlq[0]["retryable"] is False


def test_row_stays_non_terminal_while_retries_remain(client, eager_retries, captured_dlq, monkeypatch):
    """Between attempts the row must NOT read 'failed' -- a client polling
    GET /export-jobs/{id} mid-retry would otherwise be told the export failed
    and then see it succeed, which is worse than either consistent answer."""
    headers = auth_headers(client, email="retry3@example.com")
    project_id, user_id = _setup_project(client, headers)
    job = _make_job(project_id, user_id)

    observed = []
    real_path = export_tasks._csv_path

    def flaky(job_id):
        db = TestingSessionLocal()
        try:
            row = db.get(ExportJob, job_id)
            observed.append(row.status)
        finally:
            db.close()
        if len(observed) < MAX_ATTEMPTS:
            raise OSError("blip")
        return real_path(job_id)

    monkeypatch.setattr(export_tasks, "_csv_path", flaky)
    generate_project_export.delay(job.id).get()

    # Every intermediate observation is RUNNING (or PENDING on the very first
    # read before the task sets RUNNING) -- never FAILED.
    assert ExportJobStatus.FAILED not in observed
    assert len(observed) == MAX_ATTEMPTS


def test_recover_stranded_jobs_only_touches_abandoned_ones(client):
    headers = auth_headers(client, email="strand@example.com")
    project_id, user_id = _setup_project(client, headers)

    long_ago = datetime.now(timezone.utc) - timedelta(seconds=export_tasks.STRANDED_JOB_GRACE_SECONDS + 600)
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)

    old_running = _make_job(project_id, user_id, ExportJobStatus.RUNNING, created_at=long_ago)
    old_pending = _make_job(project_id, user_id, ExportJobStatus.PENDING, created_at=long_ago)
    fresh_running = _make_job(project_id, user_id, ExportJobStatus.RUNNING, created_at=just_now)
    old_completed = _make_job(project_id, user_id, ExportJobStatus.COMPLETED, created_at=long_ago)

    db = TestingSessionLocal()
    try:
        recovered = recover_stranded_export_jobs(db)
    finally:
        db.close()

    assert recovered == 2  # the two old non-terminal jobs, and only those

    db = TestingSessionLocal()
    try:
        assert db.get(ExportJob, old_running.id).status == ExportJobStatus.FAILED
        assert db.get(ExportJob, old_pending.id).status == ExportJobStatus.FAILED
        # A legitimately in-flight job must be left alone -- a false positive
        # here would report a failure for an export that then succeeds.
        assert db.get(ExportJob, fresh_running.id).status == ExportJobStatus.RUNNING
        assert db.get(ExportJob, old_completed.id).status == ExportJobStatus.COMPLETED

        msg = db.get(ExportJob, old_running.id).error_message
        # Must name the ORIGINAL stranded state, not "failed" (the value it
        # was just overwritten with) -- otherwise the message is useless.
        assert "'running'" in msg
        assert "'failed'" not in msg
        msg_pending = db.get(ExportJob, old_pending.id).error_message
        assert "'pending'" in msg_pending
    finally:
        db.close()


def test_recover_stranded_jobs_is_a_noop_when_nothing_is_stale(client):
    headers = auth_headers(client, email="strand2@example.com")
    project_id, user_id = _setup_project(client, headers)
    _make_job(project_id, user_id, ExportJobStatus.RUNNING)

    db = TestingSessionLocal()
    try:
        assert recover_stranded_export_jobs(db) == 0
    finally:
        db.close()


def test_backoff_grows_is_capped_and_is_jittered():
    """Exponential growth, bounded by the cap, and NOT a constant -- without
    jitter every task failed by the same outage retries at the same instant
    and re-overloads whatever just recovered."""
    first = [_retry_countdown(0) for _ in range(50)]
    assert len(set(round(v, 6) for v in first)) > 1, "backoff has no jitter"

    for retries in range(0, 8):
        samples = [_retry_countdown(retries) for _ in range(200)]
        ceiling = min(export_tasks.RETRY_BACKOFF_CAP_SECONDS,
                      export_tasks.RETRY_BACKOFF_BASE_SECONDS * (2 ** retries)) * 1.5
        assert all(0 < s <= ceiling + 1e-9 for s in samples), (retries, max(samples), ceiling)

    # The cap must actually bind, or a deep retry would sleep for hours and
    # look like a hung worker.
    deep = [_retry_countdown(20) for _ in range(200)]
    assert max(deep) <= export_tasks.RETRY_BACKOFF_CAP_SECONDS * 1.5


def test_dead_letter_round_trips_and_is_capped(fake_dlq_redis):
    assert dead_letter.dead_letter_count() == 0

    assert dead_letter.push_dead_letter({"job_id": 1, "reason": "boom"}) is True
    entries = dead_letter.peek_dead_letters()
    assert len(entries) == 1
    assert entries[0]["job_id"] == 1
    assert entries[0]["dead_lettered_at"]  # stamped on push

    # Newest-first ordering, so `peek` shows what just failed.
    dead_letter.push_dead_letter({"job_id": 2})
    assert [e["job_id"] for e in dead_letter.peek_dead_letters()] == [2, 1]

    # LTRIM bound: an unreplayed backlog cannot grow without limit.
    for i in range(dead_letter.MAX_DEAD_LETTERS + 50):
        dead_letter.push_dead_letter({"job_id": 1000 + i})
    assert dead_letter.dead_letter_count() == dead_letter.MAX_DEAD_LETTERS


def test_dead_letter_fails_open_when_redis_is_unreachable(monkeypatch):
    """The Postgres row is the authoritative record of a failed export; the
    Redis list is a replay convenience on top of it. So losing the convenience
    copy must not crash the worker or cost the authoritative one -- the
    opposite bias to CertiFake's Kafka DLQ, where the envelope is the only
    surviving copy and a failed publish must therefore block the commit."""
    import redis as redis_module

    class Broken:
        def pipeline(self):
            raise redis_module.exceptions.ConnectionError("simulated Redis outage")

        def llen(self, key):
            raise redis_module.exceptions.ConnectionError("simulated Redis outage")

        def lrange(self, *a, **k):
            raise redis_module.exceptions.ConnectionError("simulated Redis outage")

    monkeypatch.setattr(dead_letter, "_client", Broken())

    assert dead_letter.push_dead_letter({"job_id": 7}) is False  # no raise
    assert dead_letter.dead_letter_count() == -1  # distinguishable from "nothing failed"
    assert dead_letter.peek_dead_letters() == []


def test_attempts_is_exposed_through_the_api(client, eager_retries, captured_dlq):
    """Retry history should be visible to whoever is debugging, not only in
    worker logs."""
    headers = auth_headers(client, email="api-attempts@example.com")
    project_id, user_id = _setup_project(client, headers)
    job = _make_job(project_id, user_id)

    real_path = export_tasks._csv_path
    calls = {"n": 0}

    def flaky(job_id):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("blip")
        return real_path(job_id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(export_tasks, "_csv_path", flaky)
        generate_project_export.delay(job.id).get()

    resp = client.get(f"/export-jobs/{job.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["attempts"] == 2
