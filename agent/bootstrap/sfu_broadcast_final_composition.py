"""Final Hub composition for durable SFU broadcast control-plane services.

This module completes graphs whose local durable dependencies are available and
records why externally backed graphs remain disabled. It never substitutes
process-local stores for KMS, CA, TPM, or LiveKit capabilities.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Flask

from agent.bootstrap.sfu_broadcast_services import (
    initialize_sfu_broadcast_hub_composition,
)


_STATUS_KEY = "sfu_broadcast_final_composition_status"
_BLUEPRINTS_KEY = "sfu_broadcast_extension_blueprints"


class _SystemRouteReconciliationClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


def initialize_sfu_broadcast_final_composition(app: Flask) -> dict[str, Any]:
    """Complete all currently satisfiable Hub-owned composition graphs.

    Re-entrancy lets a deployment install external ports before a later call
    without duplicating repositories, services, blueprints, or jobs.
    """

    initialize_sfu_broadcast_hub_composition(app)
    status = dict(app.extensions.get(_STATUS_KEY) or {})
    secret = _composition_secret(app)

    _wire_durable_hub_control(app, status, secret)
    _wire_metrics(app, status)
    _wire_runtime_observation(app, status)
    _wire_turn_control(app, status, secret)
    _record_digest_key_readiness(app, status)

    status["external_capabilities"] = {
        "livekit": bool(status.get("livekit_observation_ready")),
        "kms": bool(status.get("member_digest_kms_ready")),
        "tpm": bool(status.get("tpm_attestation_ready")),
        "ca": bool(status.get("turn_ca_policy_ready")),
    }
    app.extensions[_STATUS_KEY] = status
    return status


def register_sfu_broadcast_extension_blueprints(app: Flask) -> None:
    """Register dependency-built blueprints exactly once."""

    for blueprint in tuple(app.extensions.get(_BLUEPRINTS_KEY) or ()):
        if isinstance(blueprint, Blueprint) and blueprint.name not in app.blueprints:
            app.register_blueprint(blueprint)


def _wire_durable_hub_control(
    app: Flask,
    status: dict[str, Any],
    secret: bytes | None,
) -> None:
    from agent.database import engine
    from agent.repositories.sfu_hub_control_repository import (
        SqlSfuBroadcastCommandLedger,
        SqlSfuBroadcastOperationsSnapshotRepository,
        SqlSfuFanoutReconciliationControlRepository,
        SqlSfuScopeEpochResolver,
    )
    from agent.repositories.sfu_broadcast_user_intent_repository import (
        SqlSfuBroadcastUserIntentRepository,
    )
    from agent.services.sfu_broadcast_command_execution import (
        SfuBroadcastCommandPolicyEvaluator,
        SfuBroadcastPolicyCommandAuthorizer,
        SfuBroadcastPolicyCommandExecutor,
    )
    from agent.services.sfu_broadcast_command_service import SfuBroadcastCommandService
    from agent.services.sfu_broadcast_operations_read_model import (
        SfuBroadcastOperationsReadModel,
    )

    extensions = app.extensions

    source = extensions.get("sfu_broadcast_operations_snapshot_port")
    if not _is_durable_component(source):
        source = SqlSfuBroadcastOperationsSnapshotRepository(db_engine=engine)
        extensions["sfu_broadcast_operations_snapshot_port"] = source
    if secret is not None and _is_durable_component(source):
        current = extensions.get("sfu_broadcast_operations_read_model")
        if not _is_production_component(current):
            extensions["sfu_broadcast_operations_read_model"] = (
                SfuBroadcastOperationsReadModel(
                    source=source,
                    diagnostic_secret=_derive(secret, b"operations-diagnostics"),
                )
            )
    status["operations_read_model_ready"] = _is_production_component(
        extensions.get("sfu_broadcast_operations_read_model")
    )

    ledger = extensions.get("sfu_broadcast_command_ledger")
    if not _is_durable_component(ledger):
        ledger = SqlSfuBroadcastCommandLedger(db_engine=engine)
        extensions["sfu_broadcast_command_ledger"] = ledger
    command_repository = extensions.get("sfu_broadcast_command_repository")
    if not _is_durable_component(command_repository):
        command_repository = SqlSfuBroadcastUserIntentRepository(db_engine=engine)
        extensions["sfu_broadcast_command_repository"] = command_repository
    command_scope_resolver = extensions.get("sfu_broadcast_command_scope_resolver")
    if not _is_durable_component(command_scope_resolver):
        command_scope_resolver = None
        if secret is not None:
            command_scope_resolver = SqlSfuScopeEpochResolver(
                db_engine=engine,
                identity_digest_secret=_derive(secret, b"command-scope-identity"),
            )
            extensions["sfu_broadcast_command_scope_resolver"] = (
                command_scope_resolver
            )
        else:
            extensions.pop("sfu_broadcast_command_scope_resolver", None)
    feature_policy = extensions.get("sfu_broadcast_feature_policy")
    feature_repository = extensions.get("sfu_broadcast_feature_flag_repository")
    complete_policy = (
        callable(getattr(feature_policy, "effective", None))
        and _is_durable_component(feature_repository)
        and _is_durable_component(command_scope_resolver)
    )
    if (
        secret is not None
        and complete_policy
        and _is_durable_component(command_repository)
        and _is_durable_component(ledger)
    ):
        evaluator = SfuBroadcastCommandPolicyEvaluator(
            feature_policy=feature_policy,
            room_authority=command_scope_resolver,
        )
        authorizer = SfuBroadcastPolicyCommandAuthorizer(evaluator)
        executor = SfuBroadcastPolicyCommandExecutor(
            evaluator=evaluator,
            repository=command_repository,
            diagnostic_secret=_derive(secret, b"command-audit-diagnostics"),
        )
        extensions["sfu_broadcast_command_authorization_port"] = authorizer
        extensions["sfu_broadcast_command_executor_port"] = executor
        extensions["sfu_broadcast_command_service"] = SfuBroadcastCommandService(
            authorizer=authorizer,
            executor=executor,
            ledger=ledger,
            diagnostic_secret=_derive(secret, b"command-diagnostics"),
        )
    else:
        extensions.pop("sfu_broadcast_command_authorization_port", None)
        extensions.pop("sfu_broadcast_command_executor_port", None)
        extensions.pop("sfu_broadcast_command_service", None)
    status["command_ledger_ready"] = _is_durable_component(ledger)
    status["command_repository_ready"] = _is_durable_component(command_repository)
    status["command_policy_ready"] = bool(complete_policy)
    status["command_authorization_ready"] = _is_production_component(
        extensions.get("sfu_broadcast_command_authorization_port")
    )
    status["command_executor_ready"] = _is_production_component(
        extensions.get("sfu_broadcast_command_executor_port")
    )
    status["command_service_ready"] = _is_production_component(
        extensions.get("sfu_broadcast_command_service")
    )

    if secret is not None:
        resolver = extensions.get("sfu_scope_epoch_resolver")
        if not _is_durable_component(resolver):
            resolver = SqlSfuScopeEpochResolver(
                db_engine=engine,
                identity_digest_secret=_derive(secret, b"scope-identity"),
            )
            extensions["sfu_scope_epoch_resolver"] = resolver
        extensions["sfu_capability_admission_scope"] = resolver
        extensions["sfu_layer_projection_scope_authorizer"] = resolver
    status["capability_scope_ready"] = _port(
        extensions.get("sfu_capability_admission_scope"), "resolve"
    ) is not None
    status["layer_projection_scope_ready"] = _port(
        extensions.get("sfu_layer_projection_scope_authorizer"), "authorize"
    ) is not None

    control = extensions.get("sfu_fanout_route_reconciliation_control_repository")
    if secret is not None and not _is_durable_component(control):
        control = SqlSfuFanoutReconciliationControlRepository(
            db_engine=engine,
            owner_digest_secret=_derive(secret, b"route-reconciliation-owner"),
        )
        extensions["sfu_fanout_route_reconciliation_control_repository"] = control
    if _is_durable_component(control):
        extensions["sfu_fanout_route_reconciliation_lease_port"] = control
        extensions["sfu_fanout_route_reconciliation_checkpoint_port"] = control
        extensions["sfu_fanout_route_reconciliation_outcome_port"] = control
    status["route_reconciliation_control_ready"] = _is_durable_component(control)
    _wire_durable_reconciliation_adapters(app, status, secret)
    _wire_route_reconciliation_service(app, status)
    _wire_reconciliation_jobs(app, status)


def _wire_durable_reconciliation_adapters(
    app: Flask,
    status: dict[str, Any],
    secret: bytes | None,
) -> None:
    if secret is None:
        status["route_reconciliation_scope_ready"] = False
        status["fleet_reconciliation_ports_ready"] = False
        return

    from agent.database import engine
    from agent.repositories.sfu_fleet_reconciliation_repository import (
        SfuFanoutRouteReconciliationRepositoryAdapter,
        SqlSfuFleetReconciliationMutationRepository,
        SqlSfuFleetReconciliationStateRepository,
        SqlSfuRouteReconciliationScopeRepository,
    )

    extensions = app.extensions
    scopes = extensions.get("sfu_route_reconciliation_scope_page_port")
    if not _is_durable_component(scopes):
        scopes = SqlSfuRouteReconciliationScopeRepository(
            db_engine=engine,
            cursor_signing_key=_derive(secret, b"route-reconciliation-scopes"),
        )
        extensions["sfu_route_reconciliation_scope_page_port"] = scopes
    status["route_reconciliation_scope_ready"] = _is_durable_component(scopes)

    routes = extensions.get("sfu_fanout_route_repository")
    projections = extensions.get("sfu_route_reconciliation_projection_port")
    if _is_durable_component(routes) and _is_production_component(projections):
        adapter = extensions.get("sfu_route_reconciliation_repository_adapter")
        if not _is_production_component(adapter):
            adapter = SfuFanoutRouteReconciliationRepositoryAdapter(
                routes=routes,
                projections=projections,
            )
            extensions["sfu_route_reconciliation_repository_adapter"] = adapter
        extensions["sfu_fanout_route_reconciliation_page_port"] = adapter
        extensions["sfu_fanout_route_reconciliation_authority_port"] = adapter

    runtime_state = extensions.get("sfu_fleet_runtime_route_state_port")
    runtime_mutations = extensions.get("sfu_fleet_runtime_route_mutation_port")
    capacity = _first_extension(
        extensions,
        "sfu_capacity_reservation_repository",
        "sfu_capacity_repository",
    )
    nodes = extensions.get("sfu_node_repository")
    state = extensions.get("sfu_fleet_reconciliation_state_port")
    if (
        _port(runtime_state, "observe") is not None
        and not _is_durable_component(state)
    ):
        state = SqlSfuFleetReconciliationStateRepository(
            runtime_routes=runtime_state,
            db_engine=engine,
            cursor_signing_key=_derive(secret, b"fleet-reconciliation-state"),
        )
        extensions["sfu_fleet_reconciliation_state_port"] = state
    mutations = extensions.get("sfu_fleet_reconciliation_mutation_port")
    runtime_mutations_ready = (
        _port(runtime_mutations, "fence_route") is not None
        and callable(getattr(runtime_mutations, "reconcile_desired_route", None))
    )
    if (
        runtime_mutations_ready
        and _is_durable_component(capacity)
        and _is_durable_component(nodes)
        and not _is_durable_component(mutations)
    ):
        mutations = SqlSfuFleetReconciliationMutationRepository(
            runtime_routes=runtime_mutations,
            capacity=capacity,
            nodes=nodes,
            db_engine=engine,
        )
        extensions["sfu_fleet_reconciliation_mutation_port"] = mutations
    status["fleet_reconciliation_ports_ready"] = (
        _is_durable_component(state) and _is_durable_component(mutations)
    )


def _wire_reconciliation_jobs(app: Flask, status: dict[str, Any]) -> None:
    from agent.services.sfu_broadcast_reconciliation_jobs import (
        SfuFleetReconciliationScheduledJob,
        SfuRouteReconciliationScheduledJob,
    )

    extensions = app.extensions
    state = extensions.get("sfu_fleet_reconciliation_state_port")
    mutations = extensions.get("sfu_fleet_reconciliation_mutation_port")
    fleet_job = extensions.get("sfu_fleet_reconciliation_job")
    if (
        _is_durable_component(state)
        and _is_durable_component(mutations)
        and _port(fleet_job, "run") is None
    ):
        fleet_job = SfuFleetReconciliationScheduledJob(
            state=state,
            mutations=mutations,
        )
        extensions["sfu_fleet_reconciliation_job"] = fleet_job
    status["fleet_reconciliation_job_ready"] = _port(fleet_job, "run") is not None

    reconciler = extensions.get("sfu_fanout_route_reconciliation_service")
    scopes = extensions.get("sfu_route_reconciliation_scope_page_port")
    checkpoints = extensions.get(
        "sfu_fanout_route_reconciliation_control_repository"
    )
    route_job = extensions.get("sfu_fanout_route_reconciler_job")
    if (
        _is_production_component(reconciler)
        and _is_durable_component(scopes)
        and _port(checkpoints, "load_checkpoint") is not None
        and _port(route_job, "run") is None
    ):
        route_job = SfuRouteReconciliationScheduledJob(
            reconciler=reconciler,
            scopes=scopes,
            checkpoints=checkpoints,
        )
        extensions["sfu_fanout_route_reconciler_job"] = route_job
    status["route_reconciliation_job_ready"] = _port(route_job, "run") is not None


def _wire_route_reconciliation_service(app: Flask, status: dict[str, Any]) -> None:
    extensions = app.extensions
    if _is_production_component(
        extensions.get("sfu_fanout_route_reconciliation_service")
    ):
        status["route_reconciliation_service_ready"] = True
        return

    dependencies = {
        "leases": extensions.get("sfu_fanout_route_reconciliation_lease_port"),
        "pages": extensions.get("sfu_fanout_route_reconciliation_page_port"),
        "authority": extensions.get("sfu_fanout_route_reconciliation_authority_port"),
        "checkpoints": extensions.get("sfu_fanout_route_reconciliation_checkpoint_port"),
        "outcomes": extensions.get("sfu_fanout_route_reconciliation_outcome_port"),
        "apply_routes": _first_extension(
            extensions,
            "sfu_fanout_route_apply_port",
            "sfu_broadcast_route_apply_port",
            "sfu_apply_route_port_v1",
        ),
        "update_routes": _first_extension(
            extensions,
            "sfu_fanout_route_update_port",
            "sfu_broadcast_route_update_port",
            "sfu_update_route_port_v1",
        ),
        "revoke_routes": _first_extension(
            extensions,
            "sfu_fanout_route_revoke_port",
            "sfu_broadcast_route_revoke_port",
            "sfu_revoke_route_port_v1",
        ),
        "observe_routes": _first_extension(
            extensions,
            "sfu_fanout_route_observe_port",
            "sfu_broadcast_route_observe_port",
            "sfu_observe_route_port_v1",
        ),
    }
    missing = tuple(
        name for name, value in dependencies.items() if not _is_production_component(value)
    )
    if missing:
        status["route_reconciliation_service_ready"] = False
        status["route_reconciliation_missing_ports"] = missing
        return

    from agent.services.sfu_fanout_reconciliation_service import (
        SfuFanoutReconciliationConfig,
        SfuFanoutRouteReconciliationService,
    )

    extensions["sfu_fanout_route_reconciliation_service"] = (
        SfuFanoutRouteReconciliationService(
            config=SfuFanoutReconciliationConfig(),
            clock=_SystemRouteReconciliationClock(),
            control_observer=extensions.get("sfu_broadcast_control_observer"),
            **dependencies,
        )
    )
    status["route_reconciliation_service_ready"] = True
    status.pop("route_reconciliation_missing_ports", None)


def _wire_metrics(app: Flask, status: dict[str, Any]) -> None:
    extensions = app.extensions
    policy = extensions.get("sfu_broadcast_observability_policy")
    if not _is_production_component(policy):
        status["metrics_adapter_ready"] = False
        status["metrics_service_ready"] = False
        return

    from agent.adapters.sfu_broadcast_prometheus_metrics_adapter import (
        SfuBroadcastPrometheusMetricsAdapter,
    )
    from agent.services.sfu_broadcast_metrics_service import SfuBroadcastMetricsService

    adapter = extensions.get("sfu_broadcast_metrics_adapter")
    if not _is_production_component(adapter):
        adapter = SfuBroadcastPrometheusMetricsAdapter(policy=policy)
        extensions["sfu_broadcast_metrics_adapter"] = adapter
    service = extensions.get("sfu_broadcast_metrics_service")
    if not _is_production_component(service):
        service = SfuBroadcastMetricsService(
            policy=policy,
            counter_port=adapter,
            histogram_port=adapter,
            gauge_port=adapter,
            audit_port=adapter,
        )
        extensions["sfu_broadcast_metrics_service"] = service
    status["metrics_adapter_ready"] = True
    status["metrics_service_ready"] = True


def _wire_runtime_observation(app: Flask, status: dict[str, Any]) -> None:
    extensions = app.extensions
    evaluator = extensions.get("sfu_runtime_capability_evaluator")
    store = extensions.get("sfu_runtime_observation_store")
    projection = extensions.get("sfu_runtime_capability_projection")
    if all(_is_production_component(value) for value in (evaluator, store, projection)):
        from agent.services.sfu_runtime_observation_service import (
            SfuRuntimeObservationService,
        )

        if not _is_production_component(
            extensions.get("sfu_runtime_observation_service")
        ):
            extensions["sfu_runtime_observation_service"] = SfuRuntimeObservationService(
                evaluator=evaluator,
                store=store,
                projection=projection,
                control_observer=extensions.get("sfu_broadcast_control_observer"),
            )
        status["runtime_observation_ready"] = True
    else:
        status["runtime_observation_ready"] = False

    collector = _port(extensions.get("sfu_observation_collector_job"), "run")
    attestation = extensions.get("sfu_livekit_observation_attestation")
    attested = (
        getattr(attestation, "verified", False) is True
        and getattr(attestation, "provider", "") == "livekit_control_api"
    )
    status["livekit_observation_ready"] = collector is not None and attested


def _wire_turn_control(
    app: Flask,
    status: dict[str, Any],
    secret: bytes | None,
) -> None:
    from sqlmodel import Session

    from agent.common.audit import log_audit
    from agent.database import engine
    from agent.repositories.turn_observation_cursor_repository import (
        SqlTurnObservationCursorRepository,
    )
    from agent.repositories.turn_observer_identity_repository import (
        SqlTurnObserverIdentityRepository,
    )
    from agent.repositories.turn_pool_repository import SqlTurnPoolRepository
    from agent.routes.webrtc_turn_observations import build_turn_observation_blueprint
    from agent.routes.webrtc_turn_observer_enrollment import (
        build_turn_observer_admin_blueprint,
    )
    from agent.services.turn_observation_ingestion_service import (
        TurnObservationIngestionService,
    )
    from agent.services.turn_observer_identity_service import (
        TurnObserverIdentityService,
        TurnObserverTrustPolicy,
    )
    from agent.services.turn_pool_directory import TurnPoolDirectory

    extensions = app.extensions
    identity_repository = extensions.get("turn_observer_identity_repository")
    if not _is_durable_component(identity_repository):
        identity_repository = SqlTurnObserverIdentityRepository(db_engine=engine)
        extensions["turn_observer_identity_repository"] = identity_repository
    observation_repository = extensions.get("turn_observation_cursor_repository")
    if not _is_durable_component(observation_repository):
        observation_repository = SqlTurnObservationCursorRepository(db_engine=engine)
        extensions["turn_observation_cursor_repository"] = observation_repository
    pool_repository = extensions.get("turn_pool_repository")
    if not _is_durable_component(pool_repository):
        pool_repository = SqlTurnPoolRepository(lambda: Session(engine))
        extensions["turn_pool_repository"] = pool_repository
    status["turn_repositories_ready"] = all(
        _is_durable_component(value)
        for value in (identity_repository, observation_repository, pool_repository)
    )

    policy = extensions.get("turn_observer_trust_policy")
    policy_path = str(app.config.get("TURN_OBSERVER_TRUST_POLICY_PATH") or "").strip()
    if policy is None and policy_path:
        try:
            policy = TurnObserverTrustPolicy.from_path(Path(policy_path))
        except (OSError, ValueError):
            policy = None
        if policy is not None:
            extensions["turn_observer_trust_policy"] = policy
    ca_ready = bool(policy is not None and getattr(policy, "allowed_ca_fingerprints", ()))
    status["turn_ca_policy_ready"] = ca_ready

    identity_service = extensions.get("turn_observer_identity_service")
    if secret is not None and ca_ready and not _is_production_component(identity_service):
        identity_service = TurnObserverIdentityService(
            identity_repository,
            policy=policy,
            receipt_secret=_derive(secret, b"turn-observer-receipts"),
        )
        extensions["turn_observer_identity_service"] = identity_service
    status["turn_observer_identity_ready"] = _is_production_component(identity_service)

    active_port = extensions.get("turn_observer_is_active_port")
    observer_is_active: Callable[[str, int], bool] | None
    if callable(active_port):
        observer_is_active = active_port
    else:
        observer_is_active = getattr(active_port, "is_active", None)
        if not callable(observer_is_active):
            observer_is_active = None
    directory = extensions.get("turn_pool_directory")
    if (
        secret is not None
        and observer_is_active is not None
        and not _is_production_component(directory)
    ):
        directory = TurnPoolDirectory(
            pool_repository,
            selection_hmac_key=_derive(secret, b"turn-pool-selection"),
            observer_is_active=observer_is_active,
        )
        extensions["turn_pool_directory"] = directory
    status["turn_pool_directory_ready"] = _is_production_component(directory)

    ingestion = extensions.get("turn_observation_ingestion_service")
    schema_value = str(app.config.get("TURN_OBSERVATION_SCHEMA_PATH") or "").strip()
    schema_path = Path(schema_value) if schema_value else None
    if (
        secret is not None
        and _is_production_component(identity_service)
        and schema_path is not None
        and schema_path.is_file()
        and not _is_production_component(ingestion)
    ):
        ingestion = TurnObservationIngestionService(
            observation_repository,
            identities=identity_service,
            schema_path=schema_path,
            digest_secret=_derive(secret, b"turn-observation-digests"),
            directory=directory if _is_production_component(directory) else None,
        )
        extensions["turn_observation_ingestion_service"] = ingestion
    status["turn_observation_ingestion_ready"] = _is_production_component(ingestion)

    blueprints = {
        blueprint.name: blueprint
        for blueprint in tuple(extensions.get(_BLUEPRINTS_KEY) or ())
        if isinstance(blueprint, Blueprint)
    }
    audit_logger = extensions.get("sfu_broadcast_audit_logger")
    if not callable(audit_logger):
        audit_logger = log_audit
    transport_resolver = extensions.get("turn_transport_identity_resolver")
    if (
        "webrtc_turn_observations" not in blueprints
        and callable(transport_resolver)
        and _is_production_component(ingestion)
    ):
        blueprint = build_turn_observation_blueprint(
            transport_identity_resolver=transport_resolver,
            observation_handler=ingestion.ingest,
            audit_logger=audit_logger,
        )
        blueprints[blueprint.name] = blueprint

    admin_guard = extensions.get("turn_observer_admin_guard")
    actor_resolver = extensions.get("turn_observer_admin_actor_resolver")
    command_handler = extensions.get("turn_observer_command_handler")
    if (
        "webrtc_turn_observer_enrollment" not in blueprints
        and all(callable(value) for value in (admin_guard, actor_resolver, command_handler))
        and _is_production_component(identity_service)
    ):
        blueprint = build_turn_observer_admin_blueprint(
            admin_guard=admin_guard,
            actor_resolver=actor_resolver,
            command_handler=command_handler,
            audit_logger=audit_logger,
        )
        blueprints[blueprint.name] = blueprint
    extensions[_BLUEPRINTS_KEY] = tuple(blueprints.values())
    status["turn_observation_api_ready"] = "webrtc_turn_observations" in blueprints
    status["turn_observer_admin_api_ready"] = (
        "webrtc_turn_observer_enrollment" in blueprints
    )
    status["turn_observation_collector_ready"] = _port(
        extensions.get("turn_observation_collector_job"), "run"
    ) is not None


def _record_digest_key_readiness(app: Flask, status: dict[str, Any]) -> None:
    extensions = app.extensions
    reader = extensions.get("sfu_member_digest_key_metadata_reader")
    writer = extensions.get("sfu_member_digest_key_metadata_writer")
    crypto = extensions.get("sfu_member_digest_key_crypto_port")
    contract_service = extensions.get("sfu_member_digest_key_contract_service")
    kms_attestation = extensions.get("sfu_member_digest_kms_attestation")

    ports_ready = all(
        _is_production_component(value) for value in (reader, writer, crypto)
    )
    contract_ready = _is_production_component(contract_service)
    kms_ready = (
        ports_ready
        and contract_ready
        and getattr(kms_attestation, "verified", False) is True
        and getattr(kms_attestation, "purpose", "") == "sfu_member_digest"
    )
    status["member_digest_key_ports_ready"] = ports_ready
    status["member_digest_key_contract_ready"] = contract_ready
    status["member_digest_kms_ready"] = kms_ready
    status["tpm_attestation_ready"] = (
        getattr(extensions.get("sfu_tpm_attestation"), "verified", False) is True
    )


def _composition_secret(app: Flask) -> bytes | None:
    raw = app.config.get("SFU_HUB_COMPOSITION_SECRET") or app.secret_key
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) < 32:
        return None
    return _derive(raw, b"sfu-hub-composition-root")


def _derive(secret: bytes, purpose: bytes) -> bytes:
    return hmac.new(secret, b"ananta.sfu.broadcast.v1\0" + purpose, hashlib.sha256).digest()


def _port(value: Any, method: str) -> Any | None:
    if _is_production_component(value) and callable(getattr(value, method, None)):
        return value
    return None


def _is_durable_component(value: Any) -> bool:
    if not _is_production_component(value):
        return False
    name = type(value).__name__.lower()
    module = type(value).__module__.lower()
    return "inmemory" not in name and ".testing" not in module and not module.startswith("tests")


def _is_production_component(value: Any) -> bool:
    if value is None:
        return False
    name = type(value).__name__.lower()
    module = type(value).__module__.lower()
    return (
        "mock" not in name
        and "fake" not in name
        and "stub" not in name
        and ".testing" not in module
        and not module.startswith("tests")
    )


def _first_extension(extensions: dict[str, Any], *keys: str) -> Any | None:
    for key in keys:
        value = extensions.get(key)
        if value is not None:
            return value
    return None


__all__ = [
    "initialize_sfu_broadcast_final_composition",
    "register_sfu_broadcast_extension_blueprints",
]
