import uuid
from sqlalchemy.orm import Session
from models.comment import Comment
from models.user import User, RoleEnum
from schemas.comment import CommentCreate, CommentUpdate


class CommentService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, post_id: uuid.UUID, author_id: uuid.UUID, payload: CommentCreate) -> Comment:
        comment = Comment(
            content=payload.content,
            author_id=author_id,
            post_id=post_id,
        )
        self.db.add(comment)
        self.db.commit()
        self.db.refresh(comment)
        return comment

    def update(self, comment_id: uuid.UUID, current_user: User, payload: CommentUpdate) -> Comment:
        comment = self.db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            raise ValueError("Comment not found")
        if comment.author_id != current_user.id and current_user.role != RoleEnum.ADMIN:
            raise PermissionError("Not authorized")
        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(comment, key, value)
        self.db.commit()
        self.db.refresh(comment)
        return comment

    def delete(self, comment_id: uuid.UUID, current_user: User) -> None:
        comment = self.db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            raise ValueError("Comment not found")
        if comment.author_id != current_user.id and current_user.role != RoleEnum.ADMIN:
            raise PermissionError("Not authorized")
        self.db.delete(comment)
        self.db.commit()
