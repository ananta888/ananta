import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict

from models.post import PostStatus


class PostCreate(BaseModel):
    title: str
    content: str
    status: PostStatus = PostStatus.DRAFT


class PostUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    status: PostStatus | None = None


class PostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    content: str
    status: PostStatus
    author_id: uuid.UUID
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PostList(BaseModel):
    items: list[PostResponse]
    total: int
    page: int
    page_size: int
