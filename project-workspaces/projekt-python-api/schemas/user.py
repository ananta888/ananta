import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict

from models.user import RoleEnum


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: RoleEnum = RoleEnum.READER


class UserUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    role: RoleEnum | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str
    role: RoleEnum
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserLogin(BaseModel):
    username: str
    password: str
