"""Comment endpoints, nested under issues, plus a top-level delete."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.models import User
from app.database.session import get_db
from app.schemas.comment import CommentCreate, CommentRead
from app.services import comment_service

router = APIRouter(tags=["comments"])


@router.post("/issues/{issue_id}/comments", response_model=CommentRead, status_code=status.HTTP_201_CREATED)
def add_comment(
    issue_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return comment_service.add_comment(db, issue_id, payload, current_user)


@router.get("/issues/{issue_id}/comments", response_model=list[CommentRead])
def list_comments(
    issue_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    return comment_service.list_comments(db, issue_id)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    comment_service.delete_comment(db, comment_id, current_user)
