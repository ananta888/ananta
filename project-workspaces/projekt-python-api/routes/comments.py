import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models.comment import Comment
from models.user import User, RoleEnum
from schemas.comment import CommentCreate, CommentUpdate, CommentResponse
from routes.users import get_current_user, require_role
from services.comment_service import CommentService

router = APIRouter(prefix="/posts/{post_id}/comments", tags=["Comments"])


@router.get("/", response_model=list[CommentResponse])
def list_comments(post_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Comment).filter(Comment.post_id == post_id, Comment.is_approved == True).order_by(desc(Comment.created_at)).all()


@router.post("/", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def create_comment(
    post_id: uuid.UUID,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = CommentService(db)
    return service.create(post_id, current_user.id, payload)


@router.patch("/{comment_id}", response_model=CommentResponse)
def update_comment(
    post_id: uuid.UUID,
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = CommentService(db)
    try:
        return service.update(comment_id, current_user, payload)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    post_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = CommentService(db)
    try:
        service.delete(comment_id, current_user)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
