from __future__ import annotations

import time
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class ProjectDB(SQLModel, table=True):
    __tablename__ = "projects"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_projects_team_id_teams",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("team_id", name="uq_projects_team_id"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
        sa.CheckConstraint(
            "origin IN ('native', 'legacy_source_control')",
            name="ck_projects_origin",
        ),
        sa.CheckConstraint(
            "team_id IS NULL OR team_id = project_id",
            name="ck_projects_backing_team_identity",
        ),
        sa.Index("ix_projects_tenant_status", "tenant_id", "status"),
    )

    tenant_id: str = Field(primary_key=True, max_length=191)
    project_id: str = Field(primary_key=True, max_length=191)
    name: str = Field(max_length=255)
    description: Optional[str] = Field(
        default=None,
        sa_column=sa.Column(sa.Text(), nullable=True),
    )
    status: str = Field(default="active", max_length=16)
    origin: str = Field(default="native", max_length=32)
    team_id: Optional[str] = Field(default=None, max_length=191)
    created_by_subject_id: str = Field(max_length=191)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float = Field(default_factory=time.time)
    updated_at_epoch: float = Field(default_factory=time.time)
    archived_at_epoch: Optional[float] = None


class ProjectMembershipDB(SQLModel, table=True):
    __tablename__ = "project_memberships"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_project_memberships_project",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'maintainer', 'viewer')",
            name="ck_project_memberships_role",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'revoked')",
            name="ck_project_memberships_state",
        ),
        sa.Index(
            "ix_project_memberships_subject",
            "tenant_id",
            "subject_id",
            "state",
        ),
    )

    tenant_id: str = Field(primary_key=True, max_length=191)
    project_id: str = Field(primary_key=True, max_length=191)
    subject_id: str = Field(primary_key=True, max_length=191)
    role: str = Field(max_length=16)
    state: str = Field(default="active", max_length=16)
    lock_version: int = Field(default=1, ge=1)
    created_at_epoch: float = Field(default_factory=time.time)
    updated_at_epoch: float = Field(default_factory=time.time)
