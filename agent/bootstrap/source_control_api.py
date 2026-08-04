"""Composition root for the canonical source-control v1 API."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from agent.adapters.source_control_metrics_adapter import (
    PrometheusSourceControlMetrics,
)
from agent.config import settings
from agent.database import engine
from agent.db_models.source_control import SourceRevisionDB
from agent.repositories.source_control_public_remote_repository import (
    SQLSourceControlPublicRemoteRepository,
)
from agent.repositories.source_control_workspace_registration_repository import (
    SQLSourceControlWorkspaceRegistrationRepository,
)
from agent.repositories.source_admission_receipt_repository import (
    SQLSourceAdmissionReceiptRepository,
)
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.routes.source_control_git_authorizations import (
    create_source_control_git_authorizations_blueprint,
)
from agent.routes.source_control_operations import (
    create_source_control_operations_blueprint,
)
from agent.routes.source_control_public_remotes import (
    create_source_control_public_remotes_blueprint,
)
from agent.routes.source_control_workspace_registrations import (
    create_source_control_workspace_registrations_blueprint,
)
from agent.routes.source_control_workspace_snapshots import (
    create_source_control_workspace_snapshots_blueprint,
)
from agent.routes.source_control_v1 import (
    create_source_control_legacy_alias_blueprint,
    create_source_control_v1_blueprint,
)
from agent.services.codecompass_graph_artifact_resolver import (
    get_codecompass_graph_artifact_resolver,
)
from agent.services.codecompass_graph_projection_service import (
    get_codecompass_graph_projection_service,
)
from agent.services.codecompass_graph_window_service import (
    get_codecompass_graph_window_service,
)
from agent.services.codehug_mutation_composition import (
    CodeHugMutationCompositionService,
)
from agent.services.context_policy_lifecycle_composition import (
    build_persistent_context_policy_lifecycle,
)
from agent.services.git_remote_policy_service import (
    get_git_remote_access_policy,
)
from agent.services.hub_git_authorization_provisioning import (
    HubGitAuthorizationProvisioningService,
    UnavailableHubGitAuthorizationProvisioner,
    UnavailableHubGitSecretResolver,
)
from agent.services.model_catalog_service import CatalogQuery
from agent.services.project_access_authority import SqlProjectAccessAuthority
from agent.services.ops_registry_service import get_ops_registry_service
from agent.services.rag_helper_index_service import (
    get_rag_helper_index_service,
)
from agent.services.service_registry import get_core_services
from agent.services.repository_registry import get_repository_registry
from agent.services.knowledge_index_payload_authorization import (
    KnowledgeIndexPayloadCapabilityAuthorizer,
)
from agent.services.source_access_manifest_signing import (
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)
from agent.services.source_access_manifest_keyring import (
    SourceAccessManifestKeyringError,
    load_source_access_manifest_keyring,
)
from agent.services.source_admission_revision_coordinator import (
    SourceAdmissionRevisionCoordinator,
)
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.artifact_store import get_artifact_store
from agent.services.source_filesystem_scanner import (
    ProductionFilesystemSourceScanner,
)
from agent.services.registered_workspace_source_admission import (
    RegisteredWorkspaceSourceAdmissionService,
)
from agent.services.remote_git_source_admission import (
    RemoteGitSourceAdmissionService,
    SourceScanServiceRouter,
)
from agent.services.remote_source_payload_store import (
    SQLRemoteSourcePayloadStore,
)
from agent.services.source_control_index_production_wiring import (
    build_source_control_index_production_composition,
)
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
    build_source_control_api_runtime,
)
from agent.services.source_control_artifact_download import (
    SourceControlArtifactDownloadService,
)
from agent.services.source_control_catalogs import (
    SourceControlReadCatalogService,
    SourceRegistryRegisteredWorkspaceCatalog,
)
from agent.services.source_control_workspace_catalog import (
    CompositeRegisteredWorkspaceCatalog,
    SQLRegisteredWorkspaceCatalog,
    SecureWorkspaceFolderCatalog,
)
from agent.services.source_control_workspace_registration_service import (
    SourceControlWorkspaceRegistrationService,
)
from agent.services.source_control_workspace_snapshot_service import (
    WorkspaceSnapshotUploadService,
)
from agent.services.source_control_codehug_adapters import (
    ResolvedCodeHugDestinationCatalog,
    SQLCodeHugApprovalStore,
    SQLCodeHugMutationIntentCatalog,
    SQLCodeHugRevisionCatalog,
    build_persistent_codehug_authorization,
)
from agent.services.source_control_connection_intent import (
    SourceControlConnectionIntentResolver,
)
from agent.services.source_control_content_admission import (
    SourceControlContentAdmissionService,
)
from agent.services.source_control_grant_admin import (
    SourceControlGrantAdminService,
)
from agent.services.source_control_legacy_usage import (
    BoundedLegacySourceControlUsage,
)
from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    SourceControlHealthMonitor,
    bounded_metric_labels,
    emit_source_control_audit,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
    HubBoundSourceIndexSubmissionAdapter,
    HubSourceControlOperationsAdapter,
    ScopedWorkerModelDestinationCatalog,
    build_scoped_effective_access_service,
)
from agent.services.source_control_public_remote_service import (
    SourceControlPublicRemoteService,
)
from agent.services.source_control_registered_remote_composite import (
    CompositeRegisteredRemoteCatalog,
)
from agent.services.source_control_rollout_policy import (
    SourceControlRolloutConfiguration,
    SourceControlRolloutPolicy,
    SourceControlRolloutStage,
)
from agent.services.source_control_runtime_observability import (
    SourceControlRuntimeObservability,
)
from agent.sources.hub_git_persistent_composition import (
    compose_persistent_hub_git_source_connectors,
)
from agent.sources.source_control_connector_composition import (
    build_source_control_connector_extensions,
)
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry

_LOG = logging.getLogger(__name__)
_BOUNDED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_BOUNDED_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")


class _SQLContextPolicySources:
    def __init__(self, database_engine) -> None:
        self._engine = database_engine

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> Mapping[str, Any] | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                )
            ).first()
            if row is None:
                return None
            return {
                "connector_type": row.connector_type,
                "sensitivity": row.sensitivity,
                "admission_state": row.admission_state,
            }


class _ContextPolicyDestinations:
    def __init__(self, catalog: object) -> None:
        self._catalog = catalog

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> Mapping[str, Any] | None:
        get = getattr(self._catalog, "get", None)
        if not callable(get):
            return None
        value = get(
            tenant_id=tenant_id,
            project_id=project_id,
            destination_id=destination_id,
        )
        if value is None:
            return None
        to_wire = getattr(value, "to_wire", None)
        if callable(to_wire):
            return dict(to_wire())
        if isinstance(value, Mapping):
            return dict(value)
        return None


class _SourceControlRouteDenyAudit:
    """Adapt bounded route denials to the shared content-free audit contract."""

    def __init__(self, *, health: object, metrics: object) -> None:
        self._health = health
        self._metrics = metrics

    def record_denial(self, event: Mapping[str, object]) -> None:
        reason_code = _bounded_reason(
            event.get("reason_code"), fallback="authorization_denied"
        )
        try:
            emit_source_control_audit(
                SourceControlAuditEvent(
                    operation=SourceControlAuditOperation.deny,
                    actor_id=_bounded_id(
                        event.get("actor_id")
                        or event.get("subject_id"),
                        fallback="actor",
                    ),
                    tenant_id=_bounded_id(
                        event.get("tenant_id"), fallback="tenant"
                    ),
                    project_id=_bounded_id(
                        event.get("project_id"), fallback="project"
                    ),
                    resource_kind=_bounded_id(
                        event.get("resource_kind"), fallback="resource"
                    ),
                    resource_id=_bounded_id(
                        event.get("resource_id"), fallback="collection"
                    ),
                    trace_id=_bounded_id(
                        event.get("trace_id"), fallback="route-deny"
                    ),
                    decision=SourceControlDecision.deny,
                    reason_code=reason_code,
                )
            )
            self._health.record_failure(reason_code)
            self._metrics.increment(
                "source_control_operations_total",
                bounded_metric_labels(
                    operation="deny",
                    decision="deny",
                    reason_code="authorization",
                    status="failed",
                ),
            )
        except Exception:
            _LOG.error(
                "source_control_route_deny_observability_failed",
                exc_info=True,
            )


def register_source_control_api(app) -> None:
    if settings.role != "hub":
        app.extensions["source_control_api_registration"] = {
            "ready": False,
            "reason_code": "source_control_hub_role_required",
        }
        return
    app.extensions.setdefault(
        "project_access_authority",
        SqlProjectAccessAuthority(),
    )
    """Build once in the Hub process and register the versioned blueprint."""

    if app.extensions.get("source_control_v1_registered") is True:
        return
    preconfigured_runtime = app.extensions.get("source_control_v1_runtime")
    destination_catalog = app.extensions.get(
        "source_control_destination_catalog"
    )
    if destination_catalog is None:
        destination_catalog = ScopedWorkerModelDestinationCatalog(
            engine=engine,
            model_supplier=lambda: _server_models(app),
        )
        app.extensions[
            "source_control_destination_catalog"
        ] = destination_catalog
    remote_catalog = app.extensions.get(
        "hub_git_authorization_registry"
    )
    public_remote_repository = app.extensions.get(
        "source_control_public_remote_repository"
    )
    if public_remote_repository is None:
        public_remote_repository = SQLSourceControlPublicRemoteRepository(
            session_factory=lambda: Session(engine)
        )
        app.extensions[
            "source_control_public_remote_repository"
        ] = public_remote_repository
    git_composition = app.extensions.get(
        "hub_git_connector_composition"
    )
    remote_policy = app.extensions.get(
        "hub_git_remote_policy"
    ) or get_git_remote_access_policy()
    app.extensions["hub_git_remote_policy"] = remote_policy
    secret_resolver = app.extensions.get(
        "hub_git_secret_resolver"
    ) or UnavailableHubGitSecretResolver()
    remote_payload_store = app.extensions.get("remote_source_payload_store")
    if remote_payload_store is None:
        remote_payload_store = SQLRemoteSourcePayloadStore(
            session_factory=lambda: Session(engine),
            artifact_store=get_artifact_store(),
        )
        app.extensions["remote_source_payload_store"] = remote_payload_store
    if remote_catalog is None:
        data_root = Path(str(settings.data_dir))
        persistent_git = compose_persistent_hub_git_source_connectors(
            session_factory=lambda: Session(engine),
            config={
                "hub_git_workspace_root": (
                    data_root / "source-control/git/workspaces"
                ),
                "hub_git_credential_root": (
                    data_root / "source-control/git/credentials"
                ),
                "hub_git_budgets": app.config.get("HUB_GIT_BUDGETS"),
            },
            secret_resolver=secret_resolver,
            remote_policy=remote_policy,
            additional_registered_remote_registry=(
                public_remote_repository
            ),
            payload_store=remote_payload_store,
        )
        remote_catalog = persistent_git.registry
        git_composition = persistent_git.connectors
        app.extensions[
            "hub_git_authorization_registry"
        ] = remote_catalog
        app.extensions[
            "hub_git_connector_composition"
        ] = git_composition
    connector_remote_catalog = getattr(
        git_composition,
        "registered_remotes",
        None,
    )
    connector_registry_ready = bool(
        isinstance(
            connector_remote_catalog,
            CompositeRegisteredRemoteCatalog,
        )
        and connector_remote_catalog.contains(public_remote_repository)
    )
    registered_remote_catalog = (
        connector_remote_catalog
        if connector_registry_ready
        else remote_catalog
    )
    app.extensions[
        "source_control_registered_remote_catalog"
    ] = registered_remote_catalog
    additional_connectors = (
        build_source_control_connector_extensions(
            github_repository=git_composition.github_repository,
            generic_git=git_composition.generic_git,
        )
        if git_composition is not None
        else ()
    )
    refresh = app.extensions.get("source_refresh_service")
    registry = (
        getattr(refresh, "registry", None)
        if refresh is not None
        else None
    ) or SourceRegistry()
    if refresh is None:
        refresh = SourceRefreshService(
            registry=registry,
            additional_connectors=additional_connectors,
        )
        app.extensions["source_refresh_service"] = refresh
    else:
        registered_types = frozenset(
            refresh.connector_registry.list_types()
        )
        for connector in additional_connectors:
            if connector.connector_type not in registered_types:
                refresh.connector_registry.register(connector)
    base_workspace_catalog = app.extensions.get(
        "registered_workspace_catalog"
    )
    if base_workspace_catalog is None:
        base_workspace_catalog = SourceRegistryRegisteredWorkspaceCatalog(
            registry=registry,
            registrations=get_ops_registry_service(),
        )
    workspace_registration_repository = (
        app.extensions.get(
            "source_control_workspace_registration_repository"
        )
        or SQLSourceControlWorkspaceRegistrationRepository(
            session_factory=lambda: Session(engine)
        )
    )
    workspace_folders = (
        app.extensions.get("source_control_workspace_folder_catalog")
        or SecureWorkspaceFolderCatalog(
            workspace_root=app.config.get(
                "ANANTA_WORKSPACE_ROOT",
                os.environ.get("ANANTA_WORKSPACE_ROOT"),
            )
        )
    )
    persistent_workspace_catalog = SQLRegisteredWorkspaceCatalog(
        repository=workspace_registration_repository,
        folders=workspace_folders,
    )
    workspace_catalog = CompositeRegisteredWorkspaceCatalog(
        (base_workspace_catalog, persistent_workspace_catalog)
    )
    app.extensions[
        "source_control_workspace_registration_repository"
    ] = workspace_registration_repository
    app.extensions[
        "source_control_workspace_folder_catalog"
    ] = workspace_folders
    app.extensions[
        "source_control_persistent_workspace_catalog"
    ] = persistent_workspace_catalog
    app.extensions[
        "registered_workspace_catalog"
    ] = workspace_catalog
    workspace_registrations = SourceControlWorkspaceRegistrationService(
        repository=workspace_registration_repository,
        folders=workspace_folders,
        idempotency=SQLSourceControlOperationStore(engine),
        project_access=app.extensions["project_access_authority"],
    )
    app.extensions[
        "source_control_workspace_registration_service"
    ] = workspace_registrations
    workspace_source_connector = app.extensions.get(
        "registered_workspace_source_connector"
    )
    if workspace_source_connector is None:
        from agent.sources.registered_workspace_connector import (
            RegisteredWorkspaceConnector,
        )

        workspace_source_connector = RegisteredWorkspaceConnector(
            catalog=workspace_catalog
        )
        app.extensions[
            "registered_workspace_source_connector"
        ] = workspace_source_connector
    source_scanner = app.extensions.get("source_filesystem_scanner")
    if source_scanner is None:
        source_scanner = ProductionFilesystemSourceScanner()
        app.extensions["source_filesystem_scanner"] = source_scanner
    workspace_snapshot_upload = app.extensions.get(
        "source_control_workspace_snapshot_upload_service"
    )
    if workspace_snapshot_upload is None:
        workspace_snapshot_upload = WorkspaceSnapshotUploadService(
            workspace_root=settings.hub_workspace_root,
            project_access=app.extensions["project_access_authority"],
            folders=workspace_folders,
            workspace_registrations=workspace_registrations,
            idempotency=SQLSourceControlOperationStore(engine),
            scanner=source_scanner,
        )
        app.extensions[
            "source_control_workspace_snapshot_upload_service"
        ] = workspace_snapshot_upload
    source_admission_budgets = app.extensions.get(
        "source_admission_budgets"
    )
    if not isinstance(source_admission_budgets, SourceAdmissionBudgets):
        source_admission_budgets = SourceAdmissionBudgets()
        app.extensions[
            "source_admission_budgets"
        ] = source_admission_budgets
    if app.extensions.get("source_admission_revision_coordinator") is None:
        app.extensions[
            "source_admission_revision_coordinator"
        ] = SourceAdmissionRevisionCoordinator(
            scanner=source_scanner,
            revision_repository=SQLSourceControlRepository(engine),
            receipt_repository=SQLSourceAdmissionReceiptRepository(engine),
            budgets=source_admission_budgets,
        )
    if app.extensions.get("source_scan_service") is None:
        workspace_scan_service = RegisteredWorkspaceSourceAdmissionService(
            engine=engine,
            workspace_catalog=workspace_catalog,
            workspace_connector=workspace_source_connector,
            coordinator=app.extensions[
                "source_admission_revision_coordinator"
            ],
            budgets=source_admission_budgets,
        )
        remote_scan_service = RemoteGitSourceAdmissionService(
            engine=engine,
            registry=registered_remote_catalog,
            payload_store=remote_payload_store,
            revision_repository=SQLSourceControlRepository(engine),
            receipt_repository=SQLSourceAdmissionReceiptRepository(engine),
            budgets=source_admission_budgets,
        )
        app.extensions["source_scan_service"] = SourceScanServiceRouter(
            {
                "registered_workspace": workspace_scan_service,
                "local_directory": workspace_scan_service,
                "generic_git": remote_scan_service,
                "github_repository": remote_scan_service,
            }
        )
    registered_types = frozenset(refresh.connector_registry.list_types())
    for connector in build_source_control_connector_extensions(
        registered_workspace=workspace_source_connector
    ):
        if connector.connector_type not in registered_types:
            refresh.connector_registry.register(connector)
            registered_types = registered_types | {connector.connector_type}
    connection_intents = SourceControlConnectionIntentResolver(
        workspaces=workspace_catalog,
        remotes=registered_remote_catalog,
    )
    app.extensions[
        "source_control_connection_intent_resolver"
    ] = connection_intents
    read_catalogs = SourceControlReadCatalogService(
        workspaces=workspace_catalog,
        remotes=registered_remote_catalog,
        index_profiles=get_rag_helper_index_service(),
    )
    app.extensions["source_control_read_catalogs"] = read_catalogs
    git_authorizations = HubGitAuthorizationProvisioningService(
        repository=remote_catalog,
        provider=(
            app.extensions.get("hub_git_authorization_provisioner")
            or UnavailableHubGitAuthorizationProvisioner()
        ),
        remote_policy=app.extensions["hub_git_remote_policy"],
        idempotency=SQLSourceControlOperationStore(engine),
        connector_types=refresh.connector_registry.list_types,
        secret_resolver_ready=lambda: not isinstance(
            secret_resolver,
            UnavailableHubGitSecretResolver,
        ),
    )
    app.extensions[
        "hub_git_authorization_provisioning_service"
    ] = git_authorizations
    public_remotes = SourceControlPublicRemoteService(
        repository=public_remote_repository,
        remote_policy=remote_policy,
        transport=getattr(git_composition, "transport", None),
        idempotency=SQLSourceControlOperationStore(engine),
        project_access=app.extensions["project_access_authority"],
        enabled=_public_remote_feature_enabled(app),
        connector_registry_ready=connector_registry_ready,
    )
    app.extensions[
        "source_control_public_remote_service"
    ] = public_remotes
    content_admission = SourceControlContentAdmissionService(
        engine=engine,
        idempotency=SQLSourceControlOperationStore(engine),
    )
    app.extensions[
        "source_control_content_admission"
    ] = content_admission
    context_policy = app.extensions.get(
        "source_control_context_policy_lifecycle"
    )
    if context_policy is None:
        context_policy = build_persistent_context_policy_lifecycle(
            engine=engine,
            sources=_SQLContextPolicySources(engine),
            destinations=_ContextPolicyDestinations(destination_catalog),
        )
        app.extensions[
            "source_control_context_policy_lifecycle"
        ] = context_policy
    grant_admin = app.extensions.get("source_control_grant_admin")
    if grant_admin is None:
        grant_admin = SourceControlGrantAdminService(
            engine=engine,
            destinations=destination_catalog,
            policies=context_policy,
        )
        app.extensions["source_control_grant_admin"] = grant_admin
    index_composition = app.extensions.get(
        "source_control_index_production_composition"
    )
    if index_composition is None:
        try:
            configured_signing_key = app.extensions.get(
                "source_access_signing_key"
            )
            if isinstance(configured_signing_key, SourceAccessSigningKey):
                source_access_signing_key = configured_signing_key
                source_access_verification_keys = {
                    configured_signing_key.key_id: (
                        configured_signing_key.secret
                    )
                }
            else:
                source_access_keyring = (
                    load_source_access_manifest_keyring()
                )
                source_access_signing_key = (
                    source_access_keyring.active_signing_key
                )
                source_access_verification_keys = (
                    source_access_keyring.verification_keys
                )
        except SourceAccessManifestKeyringError as exc:
            app.extensions["source_control_index_governance_readiness"] = {
                "ready": False,
                "reason_code": exc.reason_code,
            }
        else:
            app.extensions[
                "source_access_signing_key"
            ] = source_access_signing_key
            index_composition = (
                build_source_control_index_production_composition(
                    app=app,
                    engine=engine,
                    destination_catalog=destination_catalog,
                    workspace_catalog=workspace_catalog,
                    workspace_connector=workspace_source_connector,
                    scanner=source_scanner,
                    budgets=source_admission_budgets,
                    signing_key=source_access_signing_key,
                )
            )
            app.extensions[
                "source_control_index_production_composition"
            ] = index_composition
            app.extensions[
                "source_control_index_authority_planner"
            ] = index_composition.planner
            app.extensions[
                "source_control_governed_knowledge_index_job_service"
            ] = index_composition.job_service
            app.extensions[
                "knowledge_index_execution_binding_service"
            ] = index_composition.execution_binding_service
            app.extensions[
                "knowledge_index_payload_capability_authorizer"
            ] = KnowledgeIndexPayloadCapabilityAuthorizer(
                execution_binding_service=(
                    index_composition.execution_binding_service
                ),
                manifest_verifier=WorkerSourceAccessManifestVerifier(
                    source_access_verification_keys
                ),
                agent_repository=(
                    get_repository_registry().agent_repo
                ),
            )
            app.extensions["source_control_index_governance_readiness"] = {
                "ready": True,
                "reason_code": None,
            }
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
        operations = HubSourceControlOperationsAdapter(
            engine=engine,
            registry=registry,
            refresh=refresh,
            index_submission=index_submission,
            graph_resolver=get_codecompass_graph_artifact_resolver(),
            graph_projection=get_codecompass_graph_projection_service(),
            graph_window=(
                app.extensions.get("codecompass_graph_window_service")
                or get_codecompass_graph_window_service()
            ),
            scanner=app.extensions.get("source_scan_service"),
        )
        app.extensions["source_control_v1_operations"] = operations
    artifact_deletion = app.extensions.get(
        "source_control_artifact_deletion"
    )
    if artifact_deletion is None:
        artifact_deletion = ContainedArtifactDeletionService(
            engine=engine,
            artifact_root=(
                str(settings.data_dir) + "/knowledge_indices"
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
                    engine=engine,
                    destinations=destination_catalog,
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            )
        )
    )
    intents = (
        app.extensions.get("codehug_mutation_intent_catalog")
        or SQLCodeHugMutationIntentCatalog(engine)
    )
    revisions = (
        app.extensions.get("codehug_mutation_revision_catalog")
        or SQLCodeHugRevisionCatalog(engine)
    )
    codehug_destinations = (
        app.extensions.get("codehug_mutation_destination_catalog")
        or ResolvedCodeHugDestinationCatalog(destination_catalog)
    )
    approvals = (
        app.extensions.get("codehug_mutation_approval_store")
        or SQLCodeHugApprovalStore(engine)
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
                engine=engine,
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
            engine=engine,
            artifact_root=(
                str(settings.data_dir) + "/knowledge_indices"
            ),
            destinations=destination_catalog,
            effective_access=effective_access,
        )
        app.extensions[
            "source_control_artifact_downloads"
        ] = artifact_downloads
    core_runtime = (
        preconfigured_runtime.delegate
        if isinstance(
            preconfigured_runtime, SourceControlRuntimeObservability
        )
        else preconfigured_runtime
    )
    if core_runtime is None:
        core_runtime = build_source_control_api_runtime(
            engine=engine,
            access=(
                effective_access
            ),
            operations=operations,
            context_policy=context_policy,
            artifact_deletion=artifact_deletion,
            content_admission=content_admission,
            catalogs=read_catalogs,
            grants=grant_admin,
            destinations=destination_catalog,
            connection_intents=connection_intents,
            codehug_mutations=codehug_mutations,
            artifact_downloads=artifact_downloads,
        )
    rollout = _rollout_policy(app)
    health = app.extensions.get("source_control_health_monitor")
    if health is None:
        health = SourceControlHealthMonitor()
        app.extensions["source_control_health_monitor"] = health
    metrics = app.extensions.get("source_control_metrics")
    if metrics is None:
        metrics = PrometheusSourceControlMetrics()
        app.extensions["source_control_metrics"] = metrics
    if app.extensions.get("source_control_route_deny_audit") is None:
        app.extensions[
            "source_control_route_deny_audit"
        ] = _SourceControlRouteDenyAudit(
            health=health,
            metrics=metrics,
        )
    runtime = SourceControlRuntimeObservability(
        core_runtime,
        rollout=rollout,
        metrics=metrics,
        health=health,
        shadow=app.extensions.get("source_control_shadow_observer"),
    )
    app.extensions["source_control_v1_core_runtime"] = core_runtime
    app.extensions["source_control_rollout_policy"] = rollout
    app.extensions["source_control_v1_runtime"] = runtime
    app.register_blueprint(create_source_control_v1_blueprint(runtime))
    app.register_blueprint(
        create_source_control_git_authorizations_blueprint(
            git_authorizations
        )
    )
    app.register_blueprint(
        create_source_control_public_remotes_blueprint(public_remotes)
    )
    app.register_blueprint(
        create_source_control_workspace_registrations_blueprint(
            workspace_registrations
        )
    )
    app.register_blueprint(
        create_source_control_workspace_snapshots_blueprint(
            workspace_snapshot_upload
        )
    )
    app.register_blueprint(
        create_source_control_operations_blueprint(health)
    )
    if rollout.capabilities().legacy_aliases:
        legacy_usage = BoundedLegacySourceControlUsage()
        app.extensions["source_control_legacy_usage"] = legacy_usage
        app.register_blueprint(
            create_source_control_legacy_alias_blueprint(legacy_usage)
        )
    app.extensions["source_control_v1_registered"] = True


def _public_remote_feature_enabled(app) -> bool:
    value = app.config.get(
        "SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED",
        os.environ.get(
            "ANANTA_SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED",
            "false",
        ),
    )
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _server_models(app):
    """Use the existing Hub model-catalog composition, never request payloads."""

    with app.app_context():
        from agent.routes.config.providers import _model_catalog_service

        config = dict(app.config.get("AGENT_CONFIG", {}) or {})
        return _model_catalog_service().versioned_catalog(
            CatalogQuery(
                default_provider=str(
                    config.get("default_provider") or ""
                ),
                default_model=str(config.get("default_model") or ""),
                task_kind="code_review",
                timeout_seconds=3,
                cache_ttl_seconds=30,
            )
        ).models


def _rollout_policy(app) -> SourceControlRolloutPolicy:
    raw_stage = str(
        app.config.get("SOURCE_CONTROL_ROLLOUT_STAGE")
        or os.environ.get("SOURCE_CONTROL_ROLLOUT_STAGE")
        or "GITHUB"
    ).strip()
    try:
        stage = (
            SourceControlRolloutStage(int(raw_stage))
            if raw_stage.isdigit()
            else SourceControlRolloutStage[raw_stage.upper()]
        )
    except (KeyError, ValueError) as exc:
        raise RuntimeError("source_control_rollout_stage_invalid") from exc
    shadow = _configured_bool(
        app,
        "SOURCE_CONTROL_SHADOW_COMPARE_ENABLED",
        default=stage is SourceControlRolloutStage.SHADOW_READ_MODEL,
    )
    aliases = _configured_bool(
        app,
        "SOURCE_CONTROL_LEGACY_ALIASES_ENABLED",
        default=stage is not SourceControlRolloutStage.LEGACY_DISABLED,
    )
    release_report = app.extensions.get(
        "source_control_release_gate_report"
    )
    production_release_allowed = bool(
        getattr(release_report, "release_allowed", False)
    )
    return SourceControlRolloutPolicy(
        SourceControlRolloutConfiguration(
            stage=stage,
            shadow_compare_enabled=shadow,
            legacy_aliases_enabled=aliases,
            production_release_allowed=production_release_allowed,
        )
    )


def _configured_bool(app, name: str, *, default: bool) -> bool:
    value = app.config.get(name)
    if value is None:
        value = os.environ.get(name)
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name.lower()}_invalid")


def _bounded_id(value: object, *, fallback: str) -> str:
    text = str(value or "")
    if _BOUNDED_ID.fullmatch(text):
        return text
    if not text:
        return fallback
    return (
        f"{fallback}-"
        f"{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"
    )


def _bounded_reason(value: object, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return (
        normalized
        if _BOUNDED_REASON.fullmatch(normalized)
        else fallback
    )


__all__ = ["register_source_control_api"]
