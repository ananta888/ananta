"""Append-only persistence models for controlling import evidence."""

from __future__ import annotations

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class BusinessControllingProfileDB(SQLModel, table=True):
    __tablename__ = "business_controlling_profiles"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "source_revision_id",
            "revision_digest",
            name="uq_business_controlling_profile_source",
        ),
        sa.Index(
            "ix_business_controlling_profile_scope",
            "tenant_id",
            "project_id",
            "source_revision_id",
        ),
    )

    profile_digest: str = Field(primary_key=True, max_length=64)
    tenant_id: str = Field(max_length=128)
    project_id: str = Field(max_length=128)
    source_revision_id: str = Field(max_length=69)
    revision_digest: str = Field(max_length=64)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at_epoch: float


class BusinessControllingMappingDB(SQLModel, table=True):
    __tablename__ = "business_controlling_mappings"
    __table_args__ = (
        sa.UniqueConstraint("profile_digest", name="uq_business_controlling_mapping_profile"),
        sa.Index("ix_business_controlling_mapping_scope", "tenant_id", "project_id", "profile_digest"),
    )

    confirmation_digest: str = Field(primary_key=True, max_length=64)
    profile_digest: str = Field(
        foreign_key="business_controlling_profiles.profile_digest",
        max_length=64,
    )
    tenant_id: str = Field(max_length=128)
    project_id: str = Field(max_length=128)
    column_mapping: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    confirmed_by: str = Field(max_length=128)
    created_at_epoch: float


__all__ = ["BusinessControllingMappingDB", "BusinessControllingProfileDB"]
