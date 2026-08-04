"""Fail-closed Hub composition for governed knowledge-index dispatch."""

from __future__ import annotations

from typing import Any

from agent.services.knowledge_index_job_service import (
    KnowledgeIndexJobService,
)
from agent.services.source_access_enforcement import (
    SourceAccessEnforcementService,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)
from agent.services.source_access_persistence_adapter import (
    SQLSourceAccessEnforcementAdapter,
)
from agent.services.source_destination_resolution import (
    DestinationCatalogPort,
    SourceDestinationResolutionService,
)


def build_governed_knowledge_index_job_service(
    *,
    destination_catalog: DestinationCatalogPort,
    source_control_engine: Any,
    signing_key: SourceAccessSigningKey,
    execution_binding_service: Any,
    task_queue: Any | None = None,
    task_repository: Any | None = None,
    payload_store: Any | None = None,
    worker_artifact_service: Any | None = None,
    source_control_completion_projector: Any | None = None,
    worker_directory: Any | None = None,
    source_access_manifest_verifier: Any | None = None,
    allow_legacy_reusable_grants: bool = False,
    clock: Any | None = None,
) -> KnowledgeIndexJobService:
    """Compose only real catalog, persistent grant, and HMAC adapters."""

    grant_adapter = SQLSourceAccessEnforcementAdapter(
        source_control_engine,
        allow_legacy_reusable_grants=allow_legacy_reusable_grants,
    )
    manifest_verifier = (
        source_access_manifest_verifier
        if source_access_manifest_verifier is not None
        else WorkerSourceAccessManifestVerifier(
            {signing_key.key_id: signing_key.secret}
        )
    )
    kwargs = {
        "task_queue": task_queue,
        "task_repository": task_repository,
        "payload_store": payload_store,
        "worker_artifact_service": worker_artifact_service,
        "source_control_completion_projector": (
            source_control_completion_projector
        ),
        "worker_directory": worker_directory,
        "execution_binding_service": execution_binding_service,
        "destination_resolution_service": (
            SourceDestinationResolutionService(destination_catalog)
        ),
        "source_access_enforcement_service": (
            SourceAccessEnforcementService(
                grants=grant_adapter,
                consumptions=grant_adapter,
                signer=HubSourceAccessManifestSigner(signing_key),
                manifest_verifier=manifest_verifier,
                consumption_receipts=grant_adapter,
            )
        ),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return KnowledgeIndexJobService(**kwargs)


__all__ = ["build_governed_knowledge_index_job_service"]
