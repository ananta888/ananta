"""Per-principal layout preferences and revision-bound topology snapshots."""

from __future__ import annotations

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .organization_columns import json_column


class OrganizationLayoutPreferenceDB(SQLModel, table=True):
    __tablename__ = "organization_layout_preferences"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_layout_preferences_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "principal_id",
            "projection_mode",
            name="uq_organization_layout_preference",
        ),
        sa.CheckConstraint("projection_mode IN ('hierarchy', 'graph')", name="ck_organization_layout_projection_mode"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    principal_id: str = Field(index=True, max_length=191)
    projection_mode: str = Field(max_length=16)
    definition_revision: str = Field(max_length=64)
    layout_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    updated_at: float = Field(default_factory=time.time)


class OrganizationTopologySnapshotDB(SQLModel, table=True):
    __tablename__ = "organization_topology_snapshots"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_organization_topology_snapshots_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "revision", name="uq_organization_topology_snapshot_revision"
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "organization_id", "snapshot_hash", name="uq_organization_topology_snapshot_hash"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_organization_topology_snapshot_revision"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    revision: int = Field(ge=1)
    definition_revision: str = Field(max_length=64)
    snapshot_hash: str = Field(index=True, max_length=64)
    snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(dict))
    created_at: float = Field(default_factory=time.time)


__all__ = [
    "OrganizationLayoutPreferenceDB",
    "OrganizationTopologySnapshotDB",
]
