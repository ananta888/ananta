"""Durable ledger for idempotent legacy source-control adoption."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SourceRefMappingDB(SQLModel, table=True):
    __tablename__ = "source_ref_mappings"
    __table_args__ = (
        UniqueConstraint(
            "source_revision_id",
            "provenance_digest",
            name="uq_source_ref_mappings_revision_provenance",
        ),
    )

    source_ref_id: str = Field(primary_key=True, max_length=69)
    connection_id: str = Field(
        foreign_key="source_connections.connection_id",
        index=True,
        max_length=69,
    )
    source_revision_id: str = Field(
        foreign_key="source_revisions.source_revision_id",
        index=True,
        max_length=69,
    )
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    provenance_digest: str = Field(max_length=64)
    created_at_epoch: float


class SourceControlMigrationRunDB(SQLModel, table=True):
    __tablename__ = "source_control_migration_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "owner_id",
            "inventory_digest",
            name="uq_source_control_migration_scope_inventory",
        ),
    )

    migration_id: str = Field(primary_key=True, max_length=70)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    inventory_digest: str = Field(max_length=64)
    state: str = Field(index=True, max_length=32)
    cursor: int = Field(default=0, ge=0)
    total_entries: int = Field(ge=0)
    created_mapping_count: int = Field(default=0, ge=0)
    reused_mapping_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    lock_version: int = Field(default=1, ge=1)
    failure_reason: Optional[str] = Field(default=None, max_length=160)
    started_at_epoch: float
    updated_at_epoch: float
    completed_at_epoch: Optional[float] = None


class SourceControlLegacyMappingDB(SQLModel, table=True):
    __tablename__ = "source_control_legacy_mappings"
    __table_args__ = (
        UniqueConstraint(
            "migration_id",
            "sequence",
            name="uq_source_control_legacy_mappings_sequence",
        ),
    )

    mapping_id: str = Field(primary_key=True, max_length=69)
    migration_id: str = Field(
        foreign_key="source_control_migration_runs.migration_id",
        index=True,
        max_length=70,
    )
    sequence: int = Field(ge=1)
    tenant_id: str = Field(index=True, max_length=128)
    project_id: str = Field(index=True, max_length=128)
    owner_id: str = Field(index=True, max_length=128)
    legacy_kind: str = Field(index=True, max_length=32)
    legacy_key: str = Field(max_length=256)
    legacy_record_digest: str = Field(max_length=64)
    connection_id: Optional[str] = Field(default=None, max_length=69)
    source_revision_id: Optional[str] = Field(default=None, max_length=69)
    source_ref_id: Optional[str] = Field(default=None, max_length=69)
    knowledge_index_id: Optional[str] = Field(default=None, max_length=128)
    index_run_id: Optional[str] = Field(default=None, max_length=128)
    policy_snapshot_id: Optional[str] = Field(default=None, max_length=128)
    policy_version: Optional[str] = Field(default=None, max_length=128)
    created_source_ref_mapping: bool = Field(default=False)
    created_index_binding: bool = Field(default=False)
    created_run_binding: bool = Field(default=False)
    created_at_epoch: float
