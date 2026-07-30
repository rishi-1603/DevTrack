"""Business logic for Comment resources."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.models import Comment, Issue, User, UserRole
from app.schemas.comment import CommentCreate
from app.utils.exceptions import NotFoundException, PermissionDeniedException

logger = get_logger("comment_service")


def _get_issue_or_404(db: Session, issue_id: int) -> Issue:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise NotFoundException("Issue not found.")
    return issue


def add_comment(db: Session, issue_id: int, comment_in: CommentCreate, user: User) -> Comment:
    _get_issue_or_404(db, issue_id)

    comment = Comment(issue_id=issue_id, user_id=user.id, comment=comment_in.comment)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    logger.info("Comment added to issue_id=%s by user_id=%s", issue_id, user.id)
    return comment


def list_comments(db: Session, issue_id: int) -> list[Comment]:
    _get_issue_or_404(db, issue_id)
    stmt = select(Comment).where(Comment.issue_id == issue_id).order_by(Comment.created_at.asc())
    return list(db.scalars(stmt).all())


def delete_comment(db: Session, comment_id: int, user: User) -> None:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise NotFoundException("Comment not found.")

    if comment.user_id != user.id and user.role != UserRole.ADMIN:
        raise PermissionDeniedException("Only the comment author or an admin can delete this comment.")

    db.delete(comment)
    db.commit()
    logger.info("Comment id=%s deleted by user_id=%s", comment_id, user.id)
