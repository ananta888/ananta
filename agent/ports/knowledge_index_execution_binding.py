"""Authority snapshot and atomic persistence ports for knowledge-index v2 jobs."""

from __future__ import annotations

from typing import Any, Protocol

from agent.models.knowledge_index_execution_binding import (
    CurrentKnowledgeIndexAuthority,
    KnowledgeIndexCompletionProjectionRecord,
    KnowledgeIndexExecutionRecord,
)


class KnowledgeIndexAuthoritySnapshotPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        destination_id: str,
        source_access_grant_id: str,
    ) -> CurrentKnowledgeIndexAuthority: ...


class KnowledgeIndexExecutionRepositoryPort(Protocol):
    def admit(
        self,
        record: KnowledgeIndexExecutionRecord,
    ) -> tuple[KnowledgeIndexExecutionRecord, bool]: ...

    def get(self, job_id: str) -> KnowledgeIndexExecutionRecord | None: ...

    def get_by_idempotency(
        self,
        *,
        tenant_id: str,
        project_id: str,
        idempotency_key_digest: str,
    ) -> KnowledgeIndexExecutionRecord | None: ...

    def get_by_assignment(
        self,
        *,
        assignment_id: str,
        lease_id: str,
    ) -> KnowledgeIndexExecutionRecord | None: ...

    def compare_and_set(
        self,
        record: KnowledgeIndexExecutionRecord,
        *,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord: ...

    def complete_with_projection(
        self,
        *,
        record: KnowledgeIndexExecutionRecord,
        expected_lock_version: int,
        projection_digest: str,
        projection_payload: dict[str, Any],
        now_epoch_ms: int,
    ) -> tuple[
        KnowledgeIndexExecutionRecord,
        KnowledgeIndexCompletionProjectionRecord,
    ]: ...

    def get_completion_projection(
        self,
        job_id: str,
    ) -> KnowledgeIndexCompletionProjectionRecord | None: ...

    def mark_completion_projection_projected(
        self,
        *,
        job_id: str,
        expected_lock_version: int,
        expected_projection_digest: str,
        now_epoch_ms: int,
    ) -> KnowledgeIndexCompletionProjectionRecord: ...


__all__ = [
    "KnowledgeIndexAuthoritySnapshotPort",
    "KnowledgeIndexExecutionRepositoryPort",
]
