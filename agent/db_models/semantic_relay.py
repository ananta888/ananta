from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SemanticRelayEnvelopeDB(SQLModel, table=True):
    __tablename__ = "semantic_relay_envelopes"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "audience_id",
            "message_id",
            name="uq_semantic_relay_message_audience",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "audience_id",
            "traffic_class",
            "cursor",
            name="uq_semantic_relay_audience_cursor",
        ),
    )
    id: str = Field(default_factory=lambda: f"relay-{uuid.uuid4().hex}", primary_key=True)
    message_id: str = Field(index=True, max_length=128)
    tenant_id: str = Field(index=True, max_length=128)
    session_id: str = Field(index=True, max_length=128)
    epoch: int = Field(index=True)
    sender_id: str = Field(index=True, max_length=128)
    audience_id: str = Field(index=True, max_length=128)
    traffic_class: str = Field(index=True, max_length=32)
    sequence: int
    compression: str = Field(default="none", max_length=16)
    security_algorithm: str = Field(default="AES-GCM-256", max_length=32)
    key_id: str = Field(max_length=128)
    payload_bytes: int
    payload_digest: str = Field(max_length=64)
    ciphertext: str
    cursor: int = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    expires_at: float = Field(index=True)


class SemanticRelayCursorDB(SQLModel, table=True):
    __tablename__ = "semantic_relay_cursors"
    scope_key: str = Field(primary_key=True, max_length=390)
    tenant_id: str = Field(index=True, max_length=128)
    session_id: str = Field(index=True, max_length=128)
    audience_id: str = Field(index=True, max_length=128)
    traffic_class: str = Field(index=True, max_length=32)
    next_cursor: int = 0
    acknowledged_cursor: int = 0
    version: int = 1
    updated_at: float = Field(default_factory=time.time, index=True)


__all__ = ["SemanticRelayCursorDB", "SemanticRelayEnvelopeDB"]
