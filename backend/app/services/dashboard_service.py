"""Business logic for the dashboard summary, backed by a short-lived Redis cache."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.database.models import Issue, IssuePriority, IssueStatus, Project, User
from app.utils.cache import get_cache, set_cache

logger = get_logger("dashboard_service")


def _cache_key(user_id: int) -> str:
    return f"dashboard:summary:{user_id}"


def _compute_summary(db: Session, user: User) -> dict:
    owned_project_ids = list(
        db.scalars(select(Project.id).where(Project.owner_id == user.id)).all()
    )

    total_projects = len(owned_project_ids)

    if owned_project_ids:
        issue_query = select(Issue).where(Issue.project_id.in_(owned_project_ids))
        total_issues = db.scalar(
            select(func.count()).select_from(issue_query.subquery())
        ) or 0
        completed_issues = db.scalar(
            select(func.count())
            .select_from(issue_query.where(Issue.status == IssueStatus.DONE).subquery())
        ) or 0
        pending_issues = db.scalar(
            select(func.count())
            .select_from(issue_query.where(Issue.status != IssueStatus.DONE).subquery())
        ) or 0
        high_priority_issues = db.scalar(
            select(func.count())
            .select_from(issue_query.where(Issue.priority == IssuePriority.HIGH).subquery())
        ) or 0
    else:
        total_issues = completed_issues = pending_issues = high_priority_issues = 0

    return {
        "total_projects": total_projects,
        "total_issues": total_issues,
        "completed_issues": completed_issues,
        "pending_issues": pending_issues,
        "high_priority_issues": high_priority_issues,
    }


def get_dashboard_summary(db: Session, user: User) -> dict:
    """Return the dashboard summary for `user`, using Redis as a short-lived cache."""
    key = _cache_key(user.id)

    cached = get_cache(key)
    if cached is not None:
        logger.info("Dashboard summary cache hit for user_id=%s", user.id)
        return cached

    summary = _compute_summary(db, user)
    set_cache(key, summary, settings.DASHBOARD_CACHE_TTL_SECONDS)
    logger.info("Dashboard summary computed from DB for user_id=%s", user.id)
    return summary
