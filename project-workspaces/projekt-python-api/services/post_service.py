import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models.post import Post, PostStatus
from models.user import User, RoleEnum
from schemas.post import PostCreate, PostUpdate


class PostService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, author_id: uuid.UUID, payload: PostCreate) -> Post:
        post = Post(
            title=payload.title,
            content=payload.content,
            status=payload.status,
            author_id=author_id,
            published_at=datetime.now(timezone.utc) if payload.status == PostStatus.PUBLISHED else None,
        )
        self.db.add(post)
        self.db.commit()
        self.db.refresh(post)
        return post

    def update(self, post_id: uuid.UUID, current_user: User, payload: PostUpdate) -> Post:
        post = self.db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise ValueError("Post not found")
        if post.author_id != current_user.id and current_user.role != RoleEnum.ADMIN:
            raise PermissionError("Not authorized")
        update_data = payload.model_dump(exclude_unset=True)
        if "status" in update_data:
            if update_data["status"] == PostStatus.PUBLISHED and post.status != PostStatus.PUBLISHED:
                update_data["published_at"] = datetime.now(timezone.utc)
        for key, value in update_data.items():
            setattr(post, key, value)
        self.db.commit()
        self.db.refresh(post)
        return post

    def delete(self, post_id: uuid.UUID, current_user: User) -> None:
        post = self.db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise ValueError("Post not found")
        if post.author_id != current_user.id and current_user.role != RoleEnum.ADMIN:
            raise PermissionError("Not authorized")
        self.db.delete(post)
        self.db.commit()
