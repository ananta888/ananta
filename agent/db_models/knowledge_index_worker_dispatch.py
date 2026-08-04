"""Worker-owned replay receipts for governed knowledge-index dispatches."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class KnowledgeIndexWorkerDispatchReceiptDB(SQLModel, table=True):
    """Durable, Worker-scoped execute claim.

    The Hub owns schema migration only.  Runtime reads and writes are confined
    to the Worker admission repository; the shared Task row is deliberately
    not used as a replay fence.
    """

    __tablename__ = "knowledge_index_worker_dispatch_receipts"
    __table_args__ = (
        UniqueConstraint(
            "worker_id",
            "job_id",
            "assignment_id",
            "lease_id",
            "marker_digest",
            name="uq_knowledge_index_worker_dispatch_receipt_marker",
        ),
        UniqueConstraint(
            "worker_id",
            "job_id",
            name="uq_knowledge_index_worker_dispatch_receipt_job",
        ),
    )

    receipt_id: str = Field(primary_key=True, max_length=64)
    worker_id: str = Field(index=True, max_length=160)
    job_id: str = Field(index=True, max_length=192)
    assignment_id: str = Field(max_length=192)
    lease_id: str = Field(max_length=192)
    marker_digest: str = Field(max_length=64)
    manifest_binding_digest: str = Field(max_length=64)
    lease_expires_epoch_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    grant_expires_at_epoch_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    claimed_at_epoch_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    state: str = Field(
        default="claimed",
        sa_column=Column(
            String(32),
            nullable=False,
            server_default="claimed",
        ),
    )
    result_digest: str | None = Field(default=None, max_length=64)
    result_payload: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON(none_as_null=True), nullable=True),
    )
    completed_at_epoch_ms: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )


__all__ = ["KnowledgeIndexWorkerDispatchReceiptDB"]
