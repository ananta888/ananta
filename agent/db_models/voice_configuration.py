from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class VoiceConfigurationDeltaDB(SQLModel, table=True):
    __tablename__ = "voice_configuration_deltas"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "scope",
            "scope_id",
            name="uq_voice_configuration_delta_scope",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    scope: str = Field(index=True)
    scope_id: str = Field(default="", index=True)
    delta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
