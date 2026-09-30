"""Source operations, effective access, CodeHug mutations and artifact downloads.

Composes the Hub operations adapter over the source registry/refresh and
CodeCompass services, contained artifact deletion, the scoped effective
access factory, the CodeHug mutation composition and artifact downloads.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from agent.services.codecompass_graph_artifact_resolver import (
    get_codecompass_graph_artifact_resolver,
)
from agent.services.codecompass_graph_domain_catalog_service import (
    get_codecompass_graph_domain_catalog_service,
)
from agent.services.codecompass_graph_projection_service import (
    get_codecompass_graph_projection_service,
)
from agent.services.codecompass_graph_read_service import (
    CodeCompassGraphReadService,
)
from agent.services.codecompass_graph_store_cache import (
    get_codecompass_graph_store_cache,
)
from agent.services.codecompass_graph_window_service import (
    get_codecompass_graph_window_service,
)
from agent.services.codehug_mutation_composition import (
    CodeHugMutationCompositionService,
)
from agent.services.source_access_manifest_signing import (
    SourceAccessSigningKey,
)
from agent.services.source_control_artifact_download import (
    SourceControlArtifactDownloadService,
)
from agent.services.source_control_codehug_adapters import (
    ResolvedCodeHugDestinationCatalog,
    SQLCodeHugApprovalStore,
    SQLCodeHugMutationIntentCatalog,
    SQLCodeHugRevisionCatalog,
    build_persistent_codehug_authorization,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
    HubBoundSourceIndexSubmissionAdapter,
    HubSourceControlOperationsAdapter,
    build_scoped_effective_access_service,
)
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry


@dataclass(frozen=True)
class SourceControlOperationsComposition:
    operations: object
    artifact_deletion: object
    effective_access: object


@dataclass(frozen=True)
class SourceControlCodeHugComposition:
    codehug_mutations: object | None
    artifact_downloads: object


def compose_source_control_operations(
    app,
    *,
    db_engine: Engine,
    registry: SourceRegistry,
    refresh: SourceRefreshService,
    destination_catalog: object,
    data_dir: object,
) -> SourceControlOperationsComposition:
    """Compose operations, artifact deletion and effective access."""


    operations = app.extensions.get("source_control_v1_operations")
    if operations is None:
        index_submission = app.extensions.get(
            "source_control_bound_index_submission_service"
        )
        index_planner = app.extensions.get(
            "source_control_index_authority_planner"
        )
        if index_submission is None and index_planner is not None:
            index_submission = HubBoundSourceIndexSubmissionAdapter(
                planner=index_planner,
                job_service=app.extensions.get(
                    "source_control_governed_knowledge_index_job_service"
                ),
            )
            app.extensions[
                "source_control_bound_index_submission_service"
            ] = index_submission
        graph_projection = get_codecompass_graph_projection_service()
        graph_window = (
            app.extensions.get("codecompass_graph_window_service")
            or get_codecompass_graph_window_service()
        )
        graph_domains = (
            app.extensions.get("codecompass_graph_domain_catalog_service")
            or get_codecompass_graph_domain_catalog_service()
        )
        graph_read = (
            app.extensions.get("codecompass_graph_read_service")
            or CodeCompassGraphReadService(
                projection=graph_projection,
                window=graph_window,
                domains=graph_domains,
            )
        )
        operations = HubSourceControlOperationsAdapter(
            engine=db_engine,
            registry=registry,
            refresh=refresh,
            index_submission=index_submission,
            graph_resolver=get_codecompass_graph_artifact_resolver(),
            graph_projection=graph_projection,
            graph_window=graph_window,
            graph_domains=graph_domains,
            graph_read=graph_read,
            graph_store_cache=(
                app.extensions.get("codecompass_graph_store_cache")
                or get_codecompass_graph_store_cache()
            ),
            scanner=app.extensions.get("source_scan_service"),
        )
        app.extensions["source_control_v1_operations"] = operations
    artifact_deletion = app.extensions.get(
        "source_control_artifact_deletion"
    )
    if artifact_deletion is None:
        artifact_deletion = ContainedArtifactDeletionService(
            engine=db_engine,
            artifact_root=(
                str(data_dir) + "/knowledge_indices"
            ),
        )
        app.extensions[
            "source_control_artifact_deletion"
        ] = artifact_deletion
    effective_access = (
        app.extensions.get("effective_source_access_service")
        or (
            lambda *, tenant_id, project_id: (
                build_scoped_effective_access_service(
                    engine=db_engine,
                    destinations=destination_catalog,
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            )
        )
    )
    return SourceControlOperationsComposition(
        operations=operations,
        artifact_deletion=artifact_deletion,
        effective_access=effective_access,
    )


def compose_codehug_mutations_and_downloads(
    app,
    *,
    db_engine: Engine,
    destination_catalog: object,
    effective_access: object,
    data_dir: object,
) -> SourceControlCodeHugComposition:
    """Compose CodeHug mutation governance and artifact downloads."""

    intents = (
        app.extensions.get("codehug_mutation_intent_catalog")
        or SQLCodeHugMutationIntentCatalog(db_engine)
    )
    revisions = (
        app.extensions.get("codehug_mutation_revision_catalog")
        or SQLCodeHugRevisionCatalog(db_engine)
    )
    codehug_destinations = (
        app.extensions.get("codehug_mutation_destination_catalog")
        or ResolvedCodeHugDestinationCatalog(destination_catalog)
    )
    approvals = (
        app.extensions.get("codehug_mutation_approval_store")
        or SQLCodeHugApprovalStore(db_engine)
    )
    for name, value in (
        ("codehug_mutation_intent_catalog", intents),
        ("codehug_mutation_revision_catalog", revisions),
        ("codehug_mutation_destination_catalog", codehug_destinations),
        ("codehug_mutation_approval_store", approvals),
    ):
        app.extensions[name] = value
    authorization = app.extensions.get(
        "codehug_mutation_authorization_service"
    )
    if authorization is None:
        tools = app.extensions.get("codehug_mutation_tool_catalog")
        executor = app.extensions.get("codehug_mutation_executor")
        signing_key = app.extensions.get("source_access_signing_key")
        if (
            tools is not None
            and executor is not None
            and isinstance(signing_key, SourceAccessSigningKey)
        ):
            authorization = build_persistent_codehug_authorization(
                engine=db_engine,
                tools=tools,
                executor=executor,
                effective_access=effective_access,
                signing_key=signing_key,
            )
            app.extensions[
                "codehug_mutation_authorization_service"
            ] = authorization
    codehug_mutations = (
        CodeHugMutationCompositionService(
            intents=intents,
            revisions=revisions,
            destinations=codehug_destinations,
            approvals=approvals,
            authorization=authorization,
        )
        if authorization is not None
        else None
    )
    app.extensions["source_control_codehug_mutations"] = codehug_mutations
    artifact_downloads = app.extensions.get(
        "source_control_artifact_downloads"
    )
    if artifact_downloads is None:
        artifact_downloads = SourceControlArtifactDownloadService(
            engine=db_engine,
            artifact_root=(
                str(data_dir) + "/knowledge_indices"
            ),
            destinations=destination_catalog,
            effective_access=effective_access,
        )
        app.extensions[
            "source_control_artifact_downloads"
        ] = artifact_downloads
    return SourceControlCodeHugComposition(
        codehug_mutations=codehug_mutations,
        artifact_downloads=artifact_downloads,
    )


__all__ = [
    "SourceControlCodeHugComposition",
    "SourceControlOperationsComposition",
    "compose_codehug_mutations_and_downloads",
    "compose_source_control_operations",
]
