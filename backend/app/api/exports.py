"""Async CSV export endpoints: request an export, poll its status, download it."""
import os

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.rate_limit_dep import rate_limit_by_user
from app.database.models import User
from app.database.session import get_db
from app.schemas.export import ExportJobRead
from app.services import export_service

router = APIRouter(tags=["exports"])

# Exports are the most expensive operation a client can trigger (a Celery
# task, a full-table query, disk I/O) -- limited more tightly than issue/
# comment creation to bound worst-case worker load from one abusive user.
_request_export_rate_limit = rate_limit_by_user("request_export", limit=5, window_seconds=60)


@router.post(
    "/projects/{project_id}/export",
    response_model=ExportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_project_export(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_request_export_rate_limit),
):
    """Queue an asynchronous CSV export of a project's issues. Poll
    GET /export-jobs/{id} for status, then GET /export-jobs/{id}/download
    once status is 'completed'."""
    return export_service.request_export(db, project_id, current_user)


@router.get("/export-jobs/{job_id}", response_model=ExportJobRead)
def get_export_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return export_service.get_export_job(db, job_id, current_user)


@router.get("/export-jobs/{job_id}/download")
def download_export(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_path = export_service.get_completed_export_file_path(db, job_id, current_user)
    return FileResponse(
        file_path,
        media_type="text/csv",
        filename=os.path.basename(file_path),
    )
