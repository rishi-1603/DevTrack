"""Business logic for Project resources."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.models import ActivityLog, Project, User, UserRole
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.utils.exceptions import NotFoundException, PermissionDeniedException

logger = get_logger("project_service")


def create_project(db: Session, project_in: ProjectCreate, owner: User) -> Project:
    project = Project(title=project_in.title, description=project_in.description, owner_id=owner.id)
    db.add(project)
    db.commit()
    db.refresh(project)

    db.add(ActivityLog(user_id=owner.id, action=f"created project '{project.title}'"))
    db.commit()
    logger.info("Project created: %s (id=%s) by user_id=%s", project.title, project.id, owner.id)
    return project


def get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundException("Project not found.")
    return project


def list_projects(db: Session, search: str | None = None) -> list[Project]:
    stmt = select(Project)
    if search:
        stmt = stmt.where(Project.title.ilike(f"%{search}%"))
    stmt = stmt.order_by(Project.created_at.desc())
    return list(db.scalars(stmt).all())


def _ensure_can_modify(project: Project, user: User) -> None:
    if project.owner_id != user.id and user.role != UserRole.ADMIN:
        raise PermissionDeniedException("Only the project owner or an admin can perform this action.")


def update_project(db: Session, project_id: int, project_in: ProjectUpdate, user: User) -> Project:
    project = get_project(db, project_id)
    _ensure_can_modify(project, user)

    update_data = project_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    db.add(project)
    db.commit()
    db.refresh(project)
    logger.info("Project updated: id=%s by user_id=%s", project.id, user.id)
    return project


def delete_project(db: Session, project_id: int, user: User) -> None:
    project = get_project(db, project_id)
    _ensure_can_modify(project, user)

    db.delete(project)
    db.commit()
    logger.info("Project deleted: id=%s by user_id=%s", project_id, user.id)
