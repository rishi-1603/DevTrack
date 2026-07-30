"""Project CRUD endpoints."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.models import User
from app.database.session import get_db
from app.schemas.project import ProjectCreate, ProjectList, ProjectRead, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return project_service.create_project(db, payload, current_user)


@router.get("", response_model=ProjectList)
def list_projects(
    search: str | None = Query(default=None, description="Search by project title"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    projects = project_service.list_projects(db, search=search)
    return ProjectList(total=len(projects), items=projects)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    return project_service.get_project(db, project_id)


@router.put("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return project_service.update_project(db, project_id, payload, current_user)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    project_service.delete_project(db, project_id, current_user)
