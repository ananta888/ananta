"""Composition root for the canonical source-control v1 API.

``register_source_control_api`` stays the single canonical composition
root: it composes the Git remote side, connection intents, catalogs,
grants and Context Policy itself, builds the API runtime and registers
every Source Control blueprint. Cohesive composition steps are delegated
to focused bootstrap modules that receive their inputs explicitly:

* ``source_control_workspace_bootstrap`` -- workspace catalogs and uploads
* ``source_control_admission_bootstrap`` -- admission and scan routing
* ``source_control_index_bootstrap`` -- governed knowledge-index wiring
* ``source_control_operations_bootstrap`` -- operations, access, CodeHug
* ``source_control_bootstrap_adapters`` / ``_settings`` -- small adapters
  and configuration readers
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from agent.adapters.source_control_metrics_adapter import (
    PrometheusSourceControlMetrics,
)
from agent.bootstrap.source_control_admission_bootstrap import (
    compose_source_admission,
)
from agent.bootstrap.source_control_bootstrap_adapters import (
    ContextPolicyDestinations,
    SQLContextPolicySources,
    SourceControlRouteDenyAudit,
)
from agent.bootstrap.source_control_bootstrap_settings import (
    public_remote_feature_enabled,
    server_models,
    source_control_rollout_policy,
)
from agent.bootstrap.source_control_index_bootstrap import (
    compose_source_index_governance,
)
from agent.bootstrap.source_control_operations_bootstrap import (
    compose_codehug_mutations_and_downloads,
    compose_source_control_operations,
)
from agent.bootstrap.source_control_workspace_bootstrap import (
    compose_source_control_workspaces,
)
from agent.config import settings
from agent.database import engine
from agent.repositories.source_control_public_remote_repository import (
    SQLSourceControlPublicRemoteRepository,
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
from agent.routes.source_control_v1 import (
    create_source_control_legacy_alias_blueprint,
    create_source_control_v1_blueprint,
)
from agent.routes.source_control_workspace_registrations import (
    create_source_control_workspace_registrations_blueprint,
)
from agent.routes.source_control_workspace_snapshots import (
    create_source_control_workspace_snapshots_blueprint,
)
from agent.services.artifact_store import get_artifact_store
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
from agent.services.hub_git_github_authorization_provider import (
    compose_github_authorization_provisioner_from_env,
)
from agent.services.project_access_authority import SqlProjectAccessAuthority
from agent.services.rag_helper_index_service import (
    get_rag_helper_index_service,
)
from agent.services.remote_source_payload_store import (
    SQLRemoteSourcePayloadStore,
)
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
    build_source_control_api_runtime,
)
from agent.services.source_control_catalogs import (
    SourceControlReadCatalogService,
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
    SourceControlHealthMonitor,
)
from agent.services.source_control_production_adapters import (
    ScopedWorkerModelDestinationCatalog,
)
from agent.services.source_control_public_remote_service import (
    SourceControlPublicRemoteService,
)
from agent.services.source_control_registered_remote_composite import (
    CompositeRegisteredRemoteCatalog,
)
from agent.services.source_control_rollout_policy import (
    SourceControlRolloutPolicy,
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
            model_supplier=lambda: server_models(app),
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
    workspaces = compose_source_control_workspaces(
        app,
        db_engine=engine,
        registry=registry,
        hub_workspace_root=settings.hub_workspace_root,
    )
    workspace_catalog = workspaces.workspace_catalog
    workspace_registrations = workspaces.workspace_registrations
    workspace_source_connector = workspaces.workspace_source_connector
    source_scanner = workspaces.source_scanner
    workspace_snapshot_upload = workspaces.workspace_snapshot_upload
    source_admission_budgets = compose_source_admission(
        app,
        db_engine=engine,
        workspace_catalog=workspace_catalog,
        workspace_source_connector=workspace_source_connector,
        source_scanner=source_scanner,
        registered_remote_catalog=registered_remote_catalog,
        remote_payload_store=remote_payload_store,
        refresh=refresh,
    )
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
    if app.extensions.get("hub_git_authorization_provisioner") is None:
        composed_github = compose_github_authorization_provisioner_from_env(
            secret_resolver=secret_resolver,
        )
        if composed_github is not None:
            github_provisioner, secret_resolver = composed_github
            app.extensions["hub_git_authorization_provisioner"] = (
                github_provisioner
            )
            app.extensions["hub_git_secret_resolver"] = secret_resolver
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
        enabled=public_remote_feature_enabled(app),
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
            sources=SQLContextPolicySources(engine),
            destinations=ContextPolicyDestinations(destination_catalog),
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
    compose_source_index_governance(
        app,
        db_engine=engine,
        destination_catalog=destination_catalog,
        workspace_catalog=workspace_catalog,
        workspace_source_connector=workspace_source_connector,
        source_scanner=source_scanner,
        source_admission_budgets=source_admission_budgets,
    )
    composed_operations = compose_source_control_operations(
        app,
        db_engine=engine,
        registry=registry,
        refresh=refresh,
        destination_catalog=destination_catalog,
        data_dir=settings.data_dir,
    )
    operations = composed_operations.operations
    artifact_deletion = composed_operations.artifact_deletion
    effective_access = composed_operations.effective_access
    codehug = compose_codehug_mutations_and_downloads(
        app,
        db_engine=engine,
        destination_catalog=destination_catalog,
        effective_access=effective_access,
        data_dir=settings.data_dir,
    )
    codehug_mutations = codehug.codehug_mutations
    artifact_downloads = codehug.artifact_downloads
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
    rollout: SourceControlRolloutPolicy = source_control_rollout_policy(app)
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
        ] = SourceControlRouteDenyAudit(
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


__all__ = ["register_source_control_api"]
