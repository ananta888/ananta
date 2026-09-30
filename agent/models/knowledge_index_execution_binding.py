"""Knowledge-index v2 execution records and authority snapshot (dependency-free)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexAuthorityBinding,
    KnowledgeIndexExecutionJob,
)


class KnowledgeIndexExecutionBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class CurrentKnowledgeIndexAuthority:
    tenant_id: str
    project_id: str
    source_revision_id: str
    source_revision_digest: str
    admission_digest: str
    policy_snapshot_id: str
    policy_snapshot_digest: str
    destination_id: str
    destination_digest: str
    source_access_grant_id: str
    source_access_grant_digest: str

    def to_binding(self) -> KnowledgeIndexAuthorityBinding:
        return KnowledgeIndexAuthorityBinding.create(**self.__dict__)


@dataclass(frozen=True)
class KnowledgeIndexExecutionRecord:
    job: KnowledgeIndexExecutionJob
    owner_id: str
    state: str
    lock_version: int
    result_digest: str | None
    updated_at_epoch_ms: int
    completed_at_epoch_ms: int | None = None


@dataclass(frozen=True)
class KnowledgeIndexCompletionProjectionRecord:
    job_id: str
    state: str
    lock_version: int
    projection_digest: str
    payload: dict[str, Any]
    created_at_epoch_ms: int
    updated_at_epoch_ms: int
    projected_at_epoch_ms: int | None = None


__all__ = [
    "CurrentKnowledgeIndexAuthority",
    "KnowledgeIndexCompletionProjectionRecord",
    "KnowledgeIndexExecutionBindingError",
    "KnowledgeIndexExecutionRecord",
]
