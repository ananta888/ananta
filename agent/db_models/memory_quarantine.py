"""Content-free lifecycle changes committed atomically with memory state."""

import time
import uuid

from sqlmodel import Field, SQLModel


class MemoryQuarantineEventDB(SQLModel, table=True):
    __tablename__ = "memory_quarantine_events"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    chunk_id: str = Field(index=True)
    channel_id: str = Field(default="hub-memory-tree", index=True)
    tenant_id: str = Field(index=True)
    action: str
    previous_lifecycle: str
    next_lifecycle: str
    reason_code: str
    policy_digest: str
    decision_audit_ref: str | None = None
    created_at: float = Field(default_factory=time.time)
