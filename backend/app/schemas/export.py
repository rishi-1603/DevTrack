"""Request/response schemas for the async CSV export job endpoints."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.database.models import ExportJobStatus


class ExportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requested_by_id: int
    status: ExportJobStatus
    row_count: int | None = None
    # How many times the Celery task actually ran for this job (1 = no retry
    # was needed). Exposed so retry behaviour is observable through the API
    # after the fact, not only in worker logs -- a job that completed on
    # attempt 3 is telling you something about your infrastructure even
    # though the user got their CSV.
    attempts: int = 0
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
