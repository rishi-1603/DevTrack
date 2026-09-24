"""Business logic for asynchronous project CSV exports."""
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.models import ExportJob, ExportJobStatus, User, UserRole
from app.services.project_service import get_project
from app.utils.exceptions import BadRequestException, NotFoundException, PermissionDeniedException

logger = get_logger("export_service")


def request_export(db: Session, project_id: int, user: User) -> ExportJob:
    """Create a pending ExportJob row and enqueue the Celery task.

    Import of the Celery task is deferred to inside this function (rather
    than at module load time) so that importing app.services.export_service
    -- and therefore the whole FastAPI app -- never requires a reachable
    Redis broker just to start up. The task is only actually sent to the
    broker when an export is requested.
    """
    get_project(db, project_id)  # raises NotFoundException if the project doesn't exist

    job = ExportJob(project_id=project_id, requested_by_id=user.id, status=ExportJobStatus.PENDING)
    db.add(job)
    db.commit()
    db.refresh(job)

    from app.tasks.export_tasks import generate_project_export

    generate_project_export.delay(job.id)
    logger.info("Export job id=%s queued for project_id=%s by user_id=%s", job.id, project_id, user.id)

    # Re-fetch rather than trust the in-memory `job` object: the task runs
    # via its own DB session (a separate worker process in production, or
    # -- under Celery's task_always_eager used in tests -- synchronously on
    # this same thread but through a distinct SessionLocal()). Either way,
    # this request's session has no way to see those changes without
    # re-querying. Without this, a client would get a stale "pending"
    # response even in cases where the job already finished by the time
    # this function returns.
    db.refresh(job)
    return job


def _ensure_can_view(job: ExportJob, user: User) -> None:
    if job.requested_by_id != user.id and job.project.owner_id != user.id and user.role != UserRole.ADMIN:
        raise PermissionDeniedException("You do not have permission to view this export job.")


def get_export_job(db: Session, job_id: int, user: User) -> ExportJob:
    job = db.get(ExportJob, job_id)
    if job is None:
        raise NotFoundException("Export job not found.")
    _ensure_can_view(job, user)
    return job


def get_completed_export_file_path(db: Session, job_id: int, user: User) -> str:
    job = get_export_job(db, job_id, user)
    if job.status != ExportJobStatus.COMPLETED or not job.file_path:
        raise BadRequestException(f"Export job is not ready for download (status: {job.status.value}).")
    return job.file_path
