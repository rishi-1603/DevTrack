"""Issue CRUD + workflow endpoints (assignment, status transitions)."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.rate_limit_dep import rate_limit_by_user
from app.database.models import IssuePriority, IssueStatus, User
from app.database.session import get_db
from app.schemas.issue import (
    IssueAssignRequest,
    IssueCreate,
    IssueList,
    IssueRead,
    IssueStatusUpdate,
    IssueUpdate,
)
from app.services import issue_service

router = APIRouter(prefix="/issues", tags=["issues"])

# Per-user (not per-IP): a single abusive account could otherwise flood a
# project/notify every assignee with junk issues. 60/minute is generous
# enough for real usage (including scripted bulk-creation during a sprint
# planning session) while still bounding worst-case abuse.
_create_issue_rate_limit = rate_limit_by_user("create_issue", limit=60, window_seconds=60)


@router.post("", response_model=IssueRead, status_code=status.HTTP_201_CREATED)
def create_issue(
    payload: IssueCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_create_issue_rate_limit),
):
    return issue_service.create_issue(db, payload, current_user)


@router.get("", response_model=IssueList)
def list_issues(
    project_id: int | None = Query(default=None),
    status_filter: IssueStatus | None = Query(default=None, alias="status"),
    priority: IssuePriority | None = Query(default=None),
    search: str | None = Query(default=None, description="Search by issue title"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    issues = issue_service.list_issues(
        db, project_id=project_id, status=status_filter, priority=priority, search=search
    )
    return IssueList(total=len(issues), items=issues)


@router.get("/{issue_id}", response_model=IssueRead)
def get_issue(
    issue_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    return issue_service.get_issue(db, issue_id)


@router.put("/{issue_id}", response_model=IssueRead)
def update_issue(
    issue_id: int,
    payload: IssueUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return issue_service.update_issue(db, issue_id, payload, current_user)


@router.delete("/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_issue(
    issue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    issue_service.delete_issue(db, issue_id, current_user)


@router.post("/{issue_id}/assign", response_model=IssueRead)
def assign_issue(
    issue_id: int,
    payload: IssueAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return issue_service.assign_issue(db, issue_id, payload.user_id, current_user)


@router.patch("/{issue_id}/status", response_model=IssueRead)
def change_issue_status(
    issue_id: int,
    payload: IssueStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return issue_service.change_issue_status(db, issue_id, payload.status, current_user)
