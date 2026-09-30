"""Wire constants and narrow ports of the worker knowledge-index job boundary.

Shared by the task handler and its execution collaborators so that none of
them has to import another collaborator's module to learn the contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES,
)
from worker.retrieval.knowledge_index_execution_guard import (
    KnowledgeIndexExecutionDeadlinePort,
)

JOB_SCHEMA = "ananta.knowledge_index_job.v1"
RESULT_SCHEMA = "ananta.knowledge_index_job_result.v1"
BOUND_JOB_SCHEMA = "ananta.knowledge_index_execution_job.v2"
BOUND_RESULT_SCHEMA = "ananta.knowledge_index_execution_result.v2"
PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
MAX_PAYLOAD_BYTES = MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES
SOURCE_ACCESS_MANIFEST_FIELD = "source_access_enforcement_manifest"


class KnowledgeIndexExecutionPort(Protocol):
    """Infrastructure port implemented by the worker's rag-helper runtime."""

    def execute(
        self,
        job: Mapping[str, Any],
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> Mapping[str, Any]: ...


class KnowledgeIndexPayloadLoaderPort(Protocol):
    def load(self, reference: Mapping[str, Any]) -> bytes: ...


class KnowledgeIndexArtifactPublisherPort(Protocol):
    def publish(
        self,
        *,
        job_id: str,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> list[dict[str, Any]]: ...


class KnowledgeIndexGraphArtifactMaterializerPort(Protocol):
    """Worker execution seam for graph artifacts derived from index outputs."""

    def materialize(
        self,
        *,
        knowledge_index: Mapping[str, Any],
        run: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
        revision_binding: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> Mapping[str, Any]: ...


class KnowledgeIndexWorkerDispatchAdmissionPort(Protocol):
    """Worker-local preparation and atomic v2 execution claim."""

    def prepare(
        self,
        *,
        task: Mapping[str, Any],
        job: Mapping[str, Any],
        request_data: Any,
        expected_phase: str,
    ) -> Any: ...

    def claim_execute(
        self,
        *,
        task_id: str,
        prepared: Any,
    ) -> Any: ...

    def complete_execute_result(
        self,
        *,
        claimed: Any,
        result_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


__all__ = [
    "BOUND_JOB_SCHEMA",
    "BOUND_RESULT_SCHEMA",
    "JOB_SCHEMA",
    "KnowledgeIndexArtifactPublisherPort",
    "KnowledgeIndexExecutionPort",
    "KnowledgeIndexGraphArtifactMaterializerPort",
    "KnowledgeIndexPayloadLoaderPort",
    "KnowledgeIndexWorkerDispatchAdmissionPort",
    "MAX_PAYLOAD_BYTES",
    "PAYLOAD_MEDIA_TYPE",
    "RESULT_SCHEMA",
    "SOURCE_ACCESS_MANIFEST_FIELD",
]
