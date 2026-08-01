"""Strict production composition for governed source knowledge indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from agent.services.source_access_manifest_signing import SourceAccessSigningKey
from agent.services.source_control_knowledge_index_composition import (
    build_governed_knowledge_index_job_service,
)
from agent.services.source_destination_resolution import DestinationCatalogPort


class StrictKnowledgeIndexCompositionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class StrictGovernedKnowledgeIndexDependencies:
    """Complete dependency set; no in-memory or unsigned fallback is allowed."""

    destination_catalog: DestinationCatalogPort
    source_control_engine: Any
    signing_key: SourceAccessSigningKey
    execution_binding_service: Any
    task_queue: Any
    task_repository: Any
    payload_store: Any
    worker_artifact_service: Any
    source_control_completion_projector: Any

    def __post_init__(self) -> None:
        required = {
            "destination_catalog": self.destination_catalog,
            "source_control_engine": self.source_control_engine,
            "signing_key": self.signing_key,
            "execution_binding_service": self.execution_binding_service,
            "task_queue": self.task_queue,
            "task_repository": self.task_repository,
            "payload_store": self.payload_store,
            "worker_artifact_service": self.worker_artifact_service,
            "source_control_completion_projector": (
                self.source_control_completion_projector
            ),
        }
        missing = sorted(name for name, value in required.items() if value is None)
        if missing:
            raise StrictKnowledgeIndexCompositionError(
                "knowledge_index_production_dependencies_required"
            )
        if not isinstance(self.signing_key, SourceAccessSigningKey):
            raise StrictKnowledgeIndexCompositionError(
                "knowledge_index_signing_key_invalid"
            )
        for component, method in (
            (self.destination_catalog, "resolve"),
            (self.execution_binding_service, "issue"),
            (self.task_queue, "ingest_task"),
            (self.payload_store, "store_payload"),
        ):
            if not callable(getattr(component, method, None)):
                raise StrictKnowledgeIndexCompositionError(
                    "knowledge_index_production_dependency_invalid"
                )


def build_strict_governed_knowledge_index_job_service(
    dependencies: StrictGovernedKnowledgeIndexDependencies,
    *,
    clock: Any | None = None,
) -> KnowledgeIndexJobService:
    """Compose the governed service without optional production dependencies."""

    return build_governed_knowledge_index_job_service(
        destination_catalog=dependencies.destination_catalog,
        source_control_engine=dependencies.source_control_engine,
        signing_key=dependencies.signing_key,
        execution_binding_service=dependencies.execution_binding_service,
        task_queue=dependencies.task_queue,
        task_repository=dependencies.task_repository,
        payload_store=dependencies.payload_store,
        worker_artifact_service=dependencies.worker_artifact_service,
        source_control_completion_projector=(
            dependencies.source_control_completion_projector
        ),
        allow_legacy_reusable_grants=False,
        clock=clock,
    )


__all__ = [
    "StrictGovernedKnowledgeIndexDependencies",
    "StrictKnowledgeIndexCompositionError",
    "build_strict_governed_knowledge_index_job_service",
]
