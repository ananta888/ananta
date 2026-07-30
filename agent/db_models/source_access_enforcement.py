"""Persistence records for grant execution modes and one-time consumption."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class SourceAccessGrantExecutionPolicyDB(SQLModel, table=True):
    __tablename__ = "source_access_grant_execution_policy"

    grant_id: str = Field(primary_key=True, max_length=80)
    grant_digest: str = Field(index=True, max_length=64)
    destination_digest: str = Field(index=True, max_length=64)
    consumption_mode: str = Field(max_length=16)
    grant_lock_version: int = Field(default=1)
    concurrency_version: int = Field(default=1)
    created_at: datetime
    updated_at: datetime


class SourceAccessGrantConsumptionDB(SQLModel, table=True):
    __tablename__ = "source_access_grant_consumption"

    grant_id: str = Field(primary_key=True, max_length=80)
    expected_version: int
    consumption_digest: str = Field(index=True, max_length=64)
    consumed_at: datetime


__all__ = [
    "SourceAccessGrantConsumptionDB",
    "SourceAccessGrantExecutionPolicyDB",
]
