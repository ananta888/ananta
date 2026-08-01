"""Fail-closed worker composition for Hub-bound knowledge-index jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from agent.services.source_access_manifest_signing import (
    WorkerSourceAccessManifestVerifier,
)
from worker.retrieval.knowledge_index_job_handler import (
    KnowledgeIndexWorkerTaskHandler,
    build_knowledge_index_task_handler,
)


_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


class GovernedKnowledgeIndexWorkerCompositionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class GovernedKnowledgeIndexWorkerSecurity:
    worker_id: str
    verification_keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        worker_id = str(self.worker_id or "").strip()
        if not _WORKER_ID.fullmatch(worker_id):
            raise GovernedKnowledgeIndexWorkerCompositionError(
                "knowledge_index_worker_id_invalid"
            )
        keys = dict(self.verification_keys or {})
        if not keys:
            raise GovernedKnowledgeIndexWorkerCompositionError(
                "knowledge_index_verification_keys_required"
            )
        verifier = WorkerSourceAccessManifestVerifier(keys)
        del verifier
        object.__setattr__(self, "worker_id", worker_id)
        object.__setattr__(
            self,
            "verification_keys",
            MappingProxyType(keys),
        )


def build_governed_knowledge_index_worker_handler(
    *,
    security: GovernedKnowledgeIndexWorkerSecurity,
    index_service: Any | None = None,
    payload_loader: Any | None = None,
    artifact_publisher: Any | None = None,
    graph_artifact_materializer: Any | None = None,
) -> KnowledgeIndexWorkerTaskHandler:
    """Build a v2 handler with mandatory identity and signature verification."""

    verifier = WorkerSourceAccessManifestVerifier(
        dict(security.verification_keys)
    )
    return build_knowledge_index_task_handler(
        index_service=index_service,
        payload_loader=payload_loader,
        artifact_publisher=artifact_publisher,
        graph_artifact_materializer=graph_artifact_materializer,
        source_access_manifest_verifier=verifier,
        worker_id=security.worker_id,
        allow_legacy_unsigned_source_dispatch=False,
    )


__all__ = [
    "GovernedKnowledgeIndexWorkerCompositionError",
    "GovernedKnowledgeIndexWorkerSecurity",
    "build_governed_knowledge_index_worker_handler",
]
