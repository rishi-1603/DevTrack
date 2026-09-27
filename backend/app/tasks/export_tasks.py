"""Celery task that generates a CSV export of a project's issues.

Runs in a separate worker process from the FastAPI app, so it opens its own
short-lived DB session (SessionLocal) rather than reusing anything from a
request's dependency-injected session, which would not exist in this
process.

Day 4 rework -- retries were declared but never happened. The task had
`max_retries=2` in its decorator and no `self.retry()` call anywhere in its
body, so the setting was inert: any exception marked the job permanently
FAILED on the first try. A transient database lock or a momentary filesystem
error was indistinguishable from a genuine bug, and the user's export was
just gone. Now:

  - Transient failures retry with exponential backoff + jitter.
  - Genuinely permanent failures (the job row does not exist) do NOT retry,
    because no number of attempts can create a missing row -- burning
    retries on it only delays the terminal state and wastes a worker slot.
  - Once attempts are exhausted the job is marked FAILED *and* pushed to a
    Redis dead-letter list (app/utils/dead_letter.py) carrying enough of the
    original request to replay by hand.
  - `attempts` is persisted on the row so retry history survives worker
    restarts and is visible through the API, rather than living only in
    Celery's in-flight `self.request.retries`.
"""
import csv
import os
import random
from datetime import datetime, timedelta, timezone

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.database.models import ExportJob, ExportJobStatus, Issue
from app.database.session import SessionLocal
from app.utils.dead_letter import push_dead_letter

logger = get_logger("export_tasks")

EXPORT_DIR = os.environ.get("DEVTRACK_EXPORT_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "exports"))

# Total attempts allowed (first try + retries). Celery's max_retries counts
# only the retries after the initial run, hence the -1.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 2.0
RETRY_BACKOFF_CAP_SECONDS = 60.0

# A job left in PENDING/RUNNING longer than this is presumed abandoned: the
# worker died mid-task (OOM kill, redeploy, hard crash) and nothing will ever
# advance it. Generously larger than celery_app.conf.task_time_limit (300s)
# so a legitimately slow export is never mistaken for a dead one -- see
# recover_stranded_export_jobs().
STRANDED_JOB_GRACE_SECONDS = 900


def _csv_path(job_id: int) -> str:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    return os.path.join(EXPORT_DIR, f"export_{job_id}.csv")


def _retry_countdown(retries_so_far: int) -> float:
    """Exponential backoff with jitter, capped.

    Jitter is not decoration: without it, every task failed by the same
    upstream outage (a database restart, a full disk) comes back at the same
    instants and re-overloads whatever just recovered. Equalising jitter
    around the exponential value keeps the average delay unchanged while
    spreading the wake-ups out.
    """
    exponential = min(RETRY_BACKOFF_CAP_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2 ** retries_so_far))
    return exponential * random.uniform(0.5, 1.5)


@celery_app.task(
    name="app.tasks.export_tasks.generate_project_export",
    bind=True,
    max_retries=MAX_ATTEMPTS - 1,
)
def generate_project_export(self, job_id: int) -> dict:
    """Generate the CSV for ExportJob `job_id`, updating its row on completion/failure."""
    db = SessionLocal()
    try:
        job = db.get(ExportJob, job_id)
        if job is None:
            # Permanent: retrying cannot make a missing row appear. Record the
            # terminal state and stop rather than burning the retry budget.
            logger.error("export job id=%s not found -- permanent failure, not retrying", job_id)
            push_dead_letter(
                {
                    "job_id": job_id,
                    "reason": "job row not found",
                    "attempts": self.request.retries + 1,
                    "retryable": False,
                }
            )
            return {"status": "failed", "reason": "job not found", "attempts": self.request.retries + 1}

        job.status = ExportJobStatus.RUNNING
        job.attempts = self.request.retries + 1
        db.add(job)
        db.commit()

        issues = db.query(Issue).filter(Issue.project_id == job.project_id).order_by(Issue.id).all()

        path = _csv_path(job_id)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["id", "title", "description", "priority", "status", "assigned_to", "due_date", "created_at"]
            )
            for issue in issues:
                writer.writerow(
                    [
                        issue.id,
                        issue.title,
                        issue.description or "",
                        issue.priority.value,
                        issue.status.value,
                        issue.assignee.email if issue.assignee else "",
                        issue.due_date.isoformat() if issue.due_date else "",
                        issue.created_at.isoformat(),
                    ]
                )

        job.status = ExportJobStatus.COMPLETED
        job.file_path = path
        job.row_count = len(issues)
        job.completed_at = datetime.now(timezone.utc)
        # A retry that eventually succeeded must not leave a stale error
        # message on the row implying it failed.
        job.error_message = None
        db.add(job)
        db.commit()
        logger.info(
            "export job id=%s completed: %s rows -> %s (attempt %d)",
            job_id, len(issues), path, self.request.retries + 1,
        )
        return {"status": "completed", "row_count": len(issues), "attempts": self.request.retries + 1}

    except Exception as exc:  # noqa: BLE001 -- must not let a worker crash silently
        db.rollback()
        attempt = self.request.retries + 1

        if self.request.retries < self.max_retries:
            # Transient path: leave the row RUNNING/PENDING rather than
            # marking it FAILED, because it is not terminal yet. Flapping the
            # row to failed between retries would show a client polling
            # GET /export-jobs/{id} a failure that is still being worked on.
            countdown = _retry_countdown(self.request.retries)
            logger.warning(
                "export job id=%s attempt %d/%d failed (%s: %s) -- retrying in %.1fs",
                job_id, attempt, MAX_ATTEMPTS, type(exc).__name__, exc, countdown,
            )
            db.close()
            raise self.retry(exc=exc, countdown=countdown) from exc

        # Retry budget exhausted: now it is genuinely terminal.
        logger.error(
            "export job id=%s failed permanently after %d attempt(s): %s",
            job_id, attempt, exc, exc_info=True,
        )
        job = db.get(ExportJob, job_id)
        if job is not None:
            job.status = ExportJobStatus.FAILED
            job.attempts = attempt
            job.error_message = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
        push_dead_letter(
            {
                "job_id": job_id,
                "project_id": job.project_id if job is not None else None,
                "requested_by_id": job.requested_by_id if job is not None else None,
                "reason": str(exc)[:2000],
                "error_type": type(exc).__name__,
                "attempts": attempt,
                "retryable": True,
            }
        )
        # Deliberately NOT re-raised. The terminal state is already recorded
        # in Postgres and the dead-letter list; re-raising would only mark the
        # Celery result backend FAILURE as well, adding noise without changing
        # any outcome a caller can observe. The previous version re-raised.
        return {"status": "failed", "reason": str(exc)[:500], "attempts": attempt}
    finally:
        db.close()


def recover_stranded_export_jobs(db, stale_after_seconds: int = STRANDED_JOB_GRACE_SECONDS) -> int:
    """Mark abandoned PENDING/RUNNING jobs as FAILED. Returns how many were recovered.

    The gap this closes: if a worker is OOM-killed, redeployed, or hard-
    crashes between setting status=RUNNING and finishing, no process ever
    revisits that row. It stays RUNNING forever, and a client polling
    GET /export-jobs/{id} waits indefinitely for a completion that can never
    arrive -- the same "hang forever instead of reporting a clear failure"
    failure mode this project already avoids for HTTP-level outages.

    Called at API startup (see app/main.py). The threshold is deliberately
    far larger than celery_app.conf.task_time_limit (300s) plus broker
    visibility delay, because the cost of a false positive is real: marking a
    legitimately in-flight job FAILED while its worker is still writing the
    CSV would show a failure that then "succeeds", and the client would be
    told to retry unnecessarily. Erring slow is the correct bias here.

    Known limitation, stated rather than hidden: this recovers jobs stranded
    by a *dead worker*. It does not re-enqueue them -- a recovered job is
    terminal and the user must request the export again. Automatic
    re-enqueue would need a claim/lease mechanism (e.g. a worker heartbeat
    or Celery's own acks_late + visibility timeout) to avoid double-running
    a task whose worker is merely slow rather than dead; that is a larger
    change than this pass warrants and is listed in the README.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stranded = (
        db.query(ExportJob)
        .filter(
            ExportJob.status.in_([ExportJobStatus.PENDING, ExportJobStatus.RUNNING]),
            ExportJob.created_at < cutoff,
        )
        .all()
    )
    for job in stranded:
        # Capture the ORIGINAL state before overwriting it -- otherwise the
        # message would always read "left in 'failed' state", which is both
        # useless and actively misleading for whoever debugs it.
        original_status = job.status.value
        job.status = ExportJobStatus.FAILED
        job.error_message = (
            f"Job was left in '{original_status}' state and abandoned: the worker did not finish it "
            f"within {stale_after_seconds}s. This usually means the worker process died mid-task. "
            "Please request the export again."
        )[:2000]
        job.completed_at = datetime.now(timezone.utc)
        db.add(job)
    if stranded:
        db.commit()
        logger.warning("Recovered %d stranded export job(s) left PENDING/RUNNING", len(stranded))
    return len(stranded)
