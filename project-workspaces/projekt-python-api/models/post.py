import uuid
from datetime import datetime, timezone
import enum
from sqlalchemy import Column, String, Text, DateTime, Boolean, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from database import Base


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Post(Base):
    __tablename__ = "posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False, index=True)
    content = Column(Text, nullable=False)
    status = Column(SAEnum(PostStatus), default=PostStatus.DRAFT, nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
