"""Celery task that generates a CSV export of a project's issues.

Runs in a separate worker process from the FastAPI app, so it opens its own
short-lived DB session (SessionLocal) rather than reusing anything from a
request's dependency-injected session, which would not exist in this
process.
"""
import csv
import os
from datetime import datetime, timezone

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.database.models import ExportJob, ExportJobStatus, Issue
from app.database.session import SessionLocal

logger = get_logger("export_tasks")

EXPORT_DIR = os.environ.get("DEVTRACK_EXPORT_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "exports"))


def _csv_path(job_id: int) -> str:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    return os.path.join(EXPORT_DIR, f"export_{job_id}.csv")


@celery_app.task(name="app.tasks.export_tasks.generate_project_export", bind=True, max_retries=2)
def generate_project_export(self, job_id: int) -> dict:
    """Generate the CSV for ExportJob `job_id` and update its row on completion/failure."""
    db = SessionLocal()
    try:
        job = db.get(ExportJob, job_id)
        if job is None:
            logger.error("export job id=%s not found -- nothing to do", job_id)
            return {"status": "failed", "reason": "job not found"}

        job.status = ExportJobStatus.RUNNING
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
        db.add(job)
        db.commit()
        logger.info("export job id=%s completed: %s rows -> %s", job_id, len(issues), path)
        return {"status": "completed", "row_count": len(issues)}

    except Exception as exc:  # noqa: BLE001 -- must not let a worker crash silently
        logger.error("export job id=%s failed: %s", job_id, exc, exc_info=True)
        db.rollback()
        job = db.get(ExportJob, job_id)
        if job is not None:
            job.status = ExportJobStatus.FAILED
            job.error_message = str(exc)[:2000]
            db.add(job)
            db.commit()
        raise
    finally:
        db.close()
