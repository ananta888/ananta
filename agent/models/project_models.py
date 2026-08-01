from __future__ import annotations

from typing import Literal, Optional

from sqlmodel import Field, SQLModel

ProjectRole = Literal["owner", "maintainer", "viewer"]
ProjectStatus = Literal["active", "archived"]
ProjectOrigin = Literal["native", "legacy_source_control"]
ProjectMembershipState = Literal["active", "revoked"]


class ProjectCreateCommand(SQLModel):
    tenant_id: str = Field(min_length=1, max_length=191)
    name: str = Field(min_length=1, max_length=255)
    owner_subject_id: str = Field(min_length=1, max_length=191)
    description: Optional[str] = None
    project_id: Optional[str] = Field(default=None, min_length=1, max_length=191)
    team_id: Optional[str] = Field(default=None, min_length=1, max_length=191)


class ProjectUpdateCommand(SQLModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    expected_lock_version: Optional[int] = Field(default=None, ge=1)


class ProjectMembershipUpsertCommand(SQLModel):
    subject_id: str = Field(min_length=1, max_length=191)
    role: ProjectRole
    expected_lock_version: Optional[int] = Field(default=None, ge=1)


class ProjectRead(SQLModel):
    id: str
    name: str
    description: Optional[str]
    status: ProjectStatus
    is_active: bool
    origin: ProjectOrigin
    team_id: Optional[str]
    version: int
    created_at: float
    updated_at: float
    archived_at: Optional[float]


class ProjectMembershipRead(SQLModel):
    subject_id: str
    role: ProjectRole
    state: ProjectMembershipState
    version: int
    created_at: float
    updated_at: float
