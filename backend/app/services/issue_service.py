"""Business logic for Issue resources, including workflow transitions."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.models import ActivityLog, Issue, IssueStatus, Project, User, UserRole
from app.schemas.issue import IssueCreate, IssueUpdate
from app.utils.exceptions import BadRequestException, NotFoundException, PermissionDeniedException

logger = get_logger("issue_service")

# Allowed forward transitions for the issue workflow.
_ALLOWED_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.TODO: {IssueStatus.IN_PROGRESS},
    IssueStatus.IN_PROGRESS: {IssueStatus.DONE},
    IssueStatus.DONE: set(),
}


def _get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundException("Project not found.")
    return project


def _ensure_can_modify_issue(issue: Issue, user: User) -> None:
    project = issue.project
    if project.owner_id != user.id and user.role != UserRole.ADMIN:
        raise PermissionDeniedException("Only the project owner or an admin can modify this issue.")


def create_issue(db: Session, issue_in: IssueCreate, user: User) -> Issue:
    _get_project_or_404(db, issue_in.project_id)

    issue = Issue(
        title=issue_in.title,
        description=issue_in.description,
        priority=issue_in.priority,
        due_date=issue_in.due_date,
        project_id=issue_in.project_id,
        assigned_to=issue_in.assigned_to,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)

    db.add(ActivityLog(user_id=user.id, action=f"created issue '{issue.title}'"))
    db.commit()
    logger.info("Issue created: %s (id=%s) by user_id=%s", issue.title, issue.id, user.id)
    return issue


def get_issue(db: Session, issue_id: int) -> Issue:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise NotFoundException("Issue not found.")
    return issue


def list_issues(
    db: Session,
    project_id: int | None = None,
    status: IssueStatus | None = None,
    priority=None,
    search: str | None = None,
) -> list[Issue]:
    stmt = select(Issue)
    if project_id is not None:
        stmt = stmt.where(Issue.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Issue.status == status)
    if priority is not None:
        stmt = stmt.where(Issue.priority == priority)
    if search:
        stmt = stmt.where(Issue.title.ilike(f"%{search}%"))
    stmt = stmt.order_by(Issue.created_at.desc())
    return list(db.scalars(stmt).all())


def update_issue(db: Session, issue_id: int, issue_in: IssueUpdate, user: User) -> Issue:
    issue = get_issue(db, issue_id)
    _ensure_can_modify_issue(issue, user)

    update_data = issue_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(issue, field, value)

    db.add(issue)
    db.commit()
    db.refresh(issue)
    logger.info("Issue updated: id=%s by user_id=%s", issue.id, user.id)
    return issue


def delete_issue(db: Session, issue_id: int, user: User) -> None:
    issue = get_issue(db, issue_id)
    _ensure_can_modify_issue(issue, user)

    db.delete(issue)
    db.commit()
    logger.info("Issue deleted: id=%s by user_id=%s", issue_id, user.id)


def assign_issue(db: Session, issue_id: int, assignee_id: int, user: User) -> Issue:
    issue = get_issue(db, issue_id)
    _ensure_can_modify_issue(issue, user)

    assignee = db.get(User, assignee_id)
    if assignee is None:
        raise NotFoundException("User to assign was not found.")

    issue.assigned_to = assignee_id
    db.add(issue)
    db.commit()
    db.refresh(issue)

    db.add(ActivityLog(user_id=user.id, action=f"assigned issue '{issue.title}' to user_id={assignee_id}"))
    db.commit()
    logger.info("Issue id=%s assigned to user_id=%s by user_id=%s", issue.id, assignee_id, user.id)
    return issue


def change_issue_status(db: Session, issue_id: int, new_status: IssueStatus, user: User) -> Issue:
    issue = get_issue(db, issue_id)
    _ensure_can_modify_issue(issue, user)

    if new_status != issue.status and new_status not in _ALLOWED_TRANSITIONS.get(issue.status, set()):
        raise BadRequestException(
            f"Cannot transition issue from '{issue.status.value}' to '{new_status.value}'."
        )

    issue.status = new_status
    db.add(issue)
    db.commit()
    db.refresh(issue)

    db.add(ActivityLog(user_id=user.id, action=f"changed issue '{issue.title}' status to {new_status.value}"))
    db.commit()
    logger.info("Issue id=%s status changed to %s by user_id=%s", issue.id, new_status.value, user.id)
    return issue
