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
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
