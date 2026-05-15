import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models.post import Post, PostStatus
from models.user import User, RoleEnum
from schemas.post import PostCreate, PostUpdate, PostResponse, PostList
from routes.users import get_current_user, require_role
from services.post_service import PostService

router = APIRouter(prefix="/posts", tags=["Posts"])


@router.get("/", response_model=PostList)
def list_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: PostStatus | None = None,
    author_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Post).filter(Post.status == PostStatus.PUBLISHED)
    if status:
        query = query.filter(Post.status == status)
    if author_id:
        query = query.filter(Post.author_id == author_id)
    total = query.count()
    items = query.order_by(desc(Post.published_at)).offset((page - 1) * page_size).limit(page_size).all()
    return PostList(items=items, total=total, page=page, page_size=page_size)


@router.get("/{post_id}", response_model=PostResponse)
def get_post(post_id: uuid.UUID, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or post.status == PostStatus.ARCHIVED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.post("/", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleEnum.AUTHOR, RoleEnum.ADMIN)),
):
    service = PostService(db)
    return service.create(current_user.id, payload)


@router.patch("/{post_id}", response_model=PostResponse)
def update_post(
    post_id: uuid.UUID,
    payload: PostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = PostService(db)
    try:
        return service.update(post_id, current_user, payload)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this post")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = PostService(db)
    try:
        service.delete(post_id, current_user)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this post")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
