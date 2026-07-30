"""Dashboard summary endpoint."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.models import User
from app.database.session import get_db
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardSummary(BaseModel):
    total_projects: int
    total_issues: int
    completed_issues: int
    pending_issues: int
    high_priority_issues: int


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return aggregate stats scoped to the current user's owned projects/issues (Redis-cached)."""
    return dashboard_service.get_dashboard_summary(db, current_user)
