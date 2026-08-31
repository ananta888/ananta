"""Ports and transient errors for Hub-owned knowledge-index jobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.common.errors import TransientError


class KnowledgeIndexCompletionProjectionPending(TransientError):
    """A durable result awaits its idempotent Source-Control projection."""

    reason_code = "knowledge_index_source_projection_pending"

    def __init__(self, cause: Exception) -> None:
        projection_reason = str(
            getattr(cause, "reason_code", None) or type(cause).__name__
        )
        super().__init__(
            self.reason_code,
            details={"projection_reason_code": projection_reason},
        )


class KnowledgeIndexJobRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...
    def save(self, task: Any) -> Any: ...
    def replace_bound_knowledge_index_envelope(
        self,
        task_id: str,
        *,
        expected_envelope: dict,
        replacement_envelope: dict,
    ) -> Any: ...
    def compare_and_set_status(self, task_id: str, **options: Any) -> Any: ...


class KnowledgeIndexTaskQueuePort(Protocol):
    def ingest_task(self, **kwargs: Any) -> None: ...


class KnowledgeIndexWorkerDirectoryPort(Protocol):
    def resolve_worker_url(self, worker_id: str) -> str: ...


class KnowledgeIndexPayloadStorePort(Protocol):
    def prepare_reference(
        self,
        *,
        content: bytes,
        fingerprint: str,
    ) -> dict[str, object]: ...
    def store_payload(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> Mapping[str, Any]: ...
