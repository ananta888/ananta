"""Production composition for the Hub-owned workflow control boundary.

The visual-process API predates the runtime-neutral ``ExecutionPlan`` contract
and still exposes the small ``WorkflowBackend`` interface.  This module keeps
that API compatible while ensuring that callers never receive a Local or
Temporal backend directly: every operation is authorized and dispatched by one
process-wide :class:`WorkflowControlService` instance.

The configured backend is an infrastructure adapter only.  It cannot become a
second control plane and this module deliberately has no worker imports.

The bridge, request-scoped backend, facade, and command guards live in sibling
``workflow_*`` modules and are re-exported here; this module owns the
process-wide composition and its production wiring.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from secrets import token_bytes
from typing import Any

from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_authorized_backend import (  # noqa: F401 - public re-export
    AuthorizedWorkflowBackend,
)
from agent.services.workflow_backend import (
    WorkflowBackend,
)
from agent.services.workflow_backend_control_facade import (  # noqa: F401 - public re-export
    WorkflowBackendControlFacade,
)
from agent.services.workflow_backend_durable_run_adapter import (
    DURABLE_RUN_SIGNAL_SCHEMA,
    DURABLE_RUN_START_SCHEMA,
    WorkflowBackendDurableRunAdapter,
)
from agent.services.workflow_backend_factory import (
    WorkflowBackendConfig,
    get_workflow_backend,
    get_workflow_backend_config,
)
from agent.services.workflow_configured_backend_bridge import (  # noqa: F401 - public re-export
    ConfiguredWorkflowBackendBridge,
)
from agent.services.workflow_control_authorization_helpers import (
    ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
)
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlBindingOwnerResolver,
    WorkflowControlBindingStore,
    WorkflowControlRunBinding,
    WorkflowRouteControlAuthorization,
)
from agent.services.workflow_control_command_guards import (  # noqa: F401 - public re-export
    _FAILED_START_STATUSES,
    _TRANSITION_DRIVE_ATTEMPTS,
    _assert_client_command_bindings,
    _assert_client_command_snapshot,
    _assert_restart_safe_start_adoption,
    _authoritative_projection_binding,
    _canonical_public_status,
    _initial_start_pending_status,
    _resolve_command_step_id,
    _start_request_id,
)
from agent.services.workflow_control_command_receipt_persistence import (
    InMemoryWorkflowControlCommandReceiptStore,
    SQLAlchemyWorkflowControlCommandReceiptStore,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandReceiptStore,
    # Seam: workflow_authorized_backend resolves this name here at call time.
    validate_persisted_public_status,  # noqa: F401
)
from agent.services.workflow_control_command_verification import (
    HubSignedWorkflowCommandVerifier,
    HubVerifiedDurableCommandPort,
)
from agent.services.workflow_control_dispatch_intents import (
    WorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_dispatch_persistence import (
    InMemoryWorkflowControlDispatchIntentStore,
    SQLAlchemyWorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_production_composition import (
    production_authorization_grants as _production_authorization_grants,
)
from agent.services.workflow_control_production_composition import (
    production_binding_store as _production_binding_store,
)
from agent.services.workflow_control_production_composition import (
    production_command_key_ring as _production_command_key_ring,
)
from agent.services.workflow_control_production_composition import (
    production_command_replay_store as _production_command_replay_store,
)
from agent.services.workflow_control_production_composition import (
    production_command_transition_runtime as _production_command_transition_runtime,
)
from agent.services.workflow_control_production_composition import (
    production_dispatch_intent_store as _production_dispatch_intent_store,
)
from agent.services.workflow_control_production_composition import (
    production_read_model_projector as _production_read_model_projector,
)
from agent.services.workflow_control_production_composition import (
    production_release_admission as _production_release_admission,
)
from agent.services.workflow_control_production_composition import (
    production_rollout_policies as _production_rollout_policies,
)
from agent.services.workflow_control_production_composition import (
    production_runtime_health as _production_runtime_health,
)
from agent.services.workflow_control_production_composition import (
    production_runtime_profiles as _production_runtime_profiles,
)
from agent.services.workflow_control_production_composition import (
    production_terminal_trace_runtime as _production_terminal_trace_runtime,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_control_release_selection import (
    UnavailableWorkflowRuntimeReleaseAdmission,
    WorkflowRuntimeReleaseAdmissionPort,
)
from agent.services.workflow_control_service import (
    WorkflowControlService,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRouteAuthorizationService,
    workflow_route_authorization_service,
)
from agent.services.workflow_runtime.commands import (
    WorkflowCommandIssuer,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    InMemoryReplayNonceStore,
    ReplayNonceStore,
    SignatureSigningKeyRingPort,
)
from agent.services.workflow_runtime_bridge_registry import (
    WorkflowRuntimeBridgeRegistry,
)
from agent.services.workflow_runtime_rollout_service import (
    RolloutAwareRuntimeSelection,
    WorkflowRolloutPolicyService,
)
from agent.services.workflow_runtime_selection_composition import (
    build_configured_workflow_runtime_selection,
)
from agent.services.workflow_runtime_selection_service import (
    RuntimeHealthPort,
    RuntimeSelectionAuditPort,
    WorkflowRuntimeProfileService,
)
from agent.services.workflow_terminal_trace_reconciliation import (
    WorkflowTerminalTraceReconciler,
    WorkflowTerminalTraceStatePort,
)
from agent.services.workflow_transition_native_composition import WorkflowCommandTransitionRuntime


def build_workflow_backend_control_facade(
    backend: WorkflowBackend,
    *,
    ownership: WorkflowRouteAuthorizationService = workflow_route_authorization_service,
    bindings: WorkflowControlBindingStore | None = None,
    release_admission: WorkflowRuntimeReleaseAdmissionPort | None = None,
    command_key_ring: SignatureSigningKeyRingPort | None = None,
    command_replay_store: ReplayNonceStore | None = None,
    read_model_projector: WorkflowControlReadModelProjector | None = None,
    runtime_health: RuntimeHealthPort | None = None,
    runtime_selection_audit: RuntimeSelectionAuditPort | None = None,
    runtime_profiles: WorkflowRuntimeProfileService | None = None,
    rollout_policies: WorkflowRolloutPolicyService | None = None,
    authorization_grants: WorkflowAuthorizationGrantPort | None = None,
    dispatch_intents: WorkflowControlDispatchIntentStore | None = None,
    command_receipts: WorkflowControlCommandReceiptStore | None = None,
    command_transitions: WorkflowCommandTransitionRuntime | None = None,
    trace_state: WorkflowTerminalTraceStatePort | None = None,
    trace_reconciler: WorkflowTerminalTraceReconciler | None = None,
    register_all_runtimes: bool = False,
    temporal_backend: WorkflowBackend | None = None,
) -> WorkflowBackendControlFacade:
    """Compose focused adapters around one Hub-owned control service."""

    binding_store = bindings or InMemoryWorkflowControlBindingStore()
    ownership.set_owner_resolver(WorkflowControlBindingOwnerResolver(binding_store))
    key_ring = command_key_ring or HmacKeyRing(
        {"process-local-control": token_bytes(32)},
        active_key_id="process-local-control",
    )
    replay_store = command_replay_store or InMemoryReplayNonceStore()
    command_port = HubSignedWorkflowCommandVerifier(
        WorkflowCommandVerifier(
            key_ring,
            replay_store,
        )
    )
    durable_runs = (
        WorkflowBackendDurableRunAdapter(
            backend,
            commands=command_port,
            command_issuer=WorkflowCommandIssuer(key_ring),
        )
        if str(backend.backend_id) == "temporal"
        else None
    )
    dispatch_store = dispatch_intents
    if dispatch_store is None and (durable_runs is not None or register_all_runtimes):
        dispatch_store = (
            InMemoryWorkflowControlDispatchIntentStore(
                binding_store,
                replay_store=replay_store,
            )
            if isinstance(binding_store, InMemoryWorkflowControlBindingStore)
            else SQLAlchemyWorkflowControlDispatchIntentStore(binding_store.engine)
        )
    receipt_store = command_receipts or (
        InMemoryWorkflowControlCommandReceiptStore(
            binding_store,
            replay_store=replay_store,
        )
        if isinstance(binding_store, InMemoryWorkflowControlBindingStore)
        else SQLAlchemyWorkflowControlCommandReceiptStore(binding_store.engine)
    )
    resolved_read_models = read_model_projector or _production_read_model_projector()
    from agent.services.local_workflow_backend import LocalWorkflowBackend

    if isinstance(backend, LocalWorkflowBackend):
        from agent.database import engine
        from agent.services.native_graph_production_composition import (
            build_native_graph_workflow_control_bridge,
        )
        from agent.services.workflow_authorization_grant_service import (
            InMemoryWorkflowAuthorizationGrantService,
        )

        bridge: Any = build_native_graph_workflow_control_bridge(
            engine=getattr(binding_store, "engine", engine),
            bindings=binding_store,
            key_ring=key_ring,
            replay_store=replay_store,
            authorization_grants=(authorization_grants or InMemoryWorkflowAuthorizationGrantService()),
            read_models=resolved_read_models,
        )
    else:
        bridge = ConfiguredWorkflowBackendBridge(
            backend,
            binding_store,
            durable_runs=durable_runs,
            commands=command_port,
            read_models=resolved_read_models,
            authorization_grants=authorization_grants,
            dispatch_intents=dispatch_store,
            trace_state=trace_state,
        )
    registry = WorkflowRuntimeBridgeRegistry(binding_store)
    if register_all_runtimes:
        if temporal_backend is None:
            raise ValueError("temporal_runtime_bridge_required")
        from agent.services.workflow_control_runtime_registry_composition import (
            register_production_runtime_bridges,
        )

        register_production_runtime_bridges(
            registry=registry,
            configured_bridge=bridge,
            temporal_backend=temporal_backend,
            configured_bridge_factory=ConfiguredWorkflowBackendBridge,
            bindings=binding_store,
            key_ring=key_ring,
            replay_store=replay_store,
            authorization_grants=(authorization_grants or _production_authorization_grants()),
            read_models=resolved_read_models,
            dispatch_intents=(dispatch_intents or dispatch_store),
        )
    else:
        registry.register(
            bridge.selection_runtime_id,
            bridge,
            aliases=(bridge.runtime_id,),
        )
    capability_catalog = None
    if register_all_runtimes:
        from agent.services.workflow_runtime_capability_service import (
            default_workflow_runtime_capability_service,
        )

        capability_catalog = default_workflow_runtime_capability_service()
    selection: Any = build_configured_workflow_runtime_selection(
        backend,
        health=runtime_health,
        release_evidence=release_admission,
        audit=runtime_selection_audit,
        native_production=isinstance(backend, LocalWorkflowBackend),
        registered_runtime_ids=registry.runtime_ids,
        capability_catalog=capability_catalog,
    )
    registry.freeze()
    if rollout_policies is not None:
        selection = RolloutAwareRuntimeSelection(
            policies=rollout_policies,
            selection=selection,
        )
    control = WorkflowControlService(
        authorization=WorkflowRouteControlAuthorization(ownership),
        selection=selection,
        bridge=registry,
        runtime_profiles=runtime_profiles,
        command_issuer=WorkflowCommandIssuer(key_ring),
    )
    return WorkflowBackendControlFacade(
        control=control,
        bridge=bridge,
        bindings=binding_store,
        registry=registry,
        command_receipts=receipt_store,
        transitions=command_transitions,
        trace_reconciler=trace_reconciler,
    )


_COMPOSITION_LOCK = threading.RLock()
_COMPOSITION_KEY: tuple[str, ...] | None = None
_COMPOSITION: WorkflowBackendControlFacade | None = None


def get_workflow_backend_control_facade(
    config: WorkflowBackendConfig | None = None,
) -> WorkflowBackendControlFacade:
    """Return the single active Hub workflow-control composition."""

    global _COMPOSITION, _COMPOSITION_KEY
    resolved = config or get_workflow_backend_config()
    key = _config_key(resolved)
    if _COMPOSITION is not None and _COMPOSITION_KEY == key:
        return _COMPOSITION
    with _COMPOSITION_LOCK:
        if _COMPOSITION is None or _COMPOSITION_KEY != key:
            backend = get_workflow_backend(resolved)
            temporal_backend = (
                backend
                if backend.backend_id == "temporal"
                else get_workflow_backend(replace(resolved, backend="temporal"))
            )
            trace_runtime = _production_terminal_trace_runtime()
            _COMPOSITION = build_workflow_backend_control_facade(
                backend,
                release_admission=_production_release_admission(backend),
                runtime_health=_production_runtime_health(backend),
                runtime_profiles=_production_runtime_profiles(),
                rollout_policies=_production_rollout_policies(),
                authorization_grants=_production_authorization_grants(),
                command_key_ring=_production_command_key_ring(backend),
                command_replay_store=_production_command_replay_store(),
                bindings=_production_binding_store(),
                dispatch_intents=_production_dispatch_intent_store(),
                trace_state=(trace_runtime.state if trace_runtime is not None else None),
                trace_reconciler=(trace_runtime.reconciler if trace_runtime is not None else None),
                command_transitions=_production_command_transition_runtime(backend),
                register_all_runtimes=True,
                temporal_backend=temporal_backend,
            )
            _COMPOSITION_KEY = key
    return _COMPOSITION


def reset_workflow_backend_control_facade() -> None:
    """Test/process lifecycle hook; rebuilding never widens authorization."""

    global _COMPOSITION, _COMPOSITION_KEY
    with _COMPOSITION_LOCK:
        _COMPOSITION = None
        _COMPOSITION_KEY = None
        workflow_route_authorization_service.set_owner_resolver(None)
    from agent.services.workflow_adapter_control_facade import (
        reset_workflow_adapter_control_facade,
    )

    reset_workflow_adapter_control_facade()


def _config_key(config: WorkflowBackendConfig) -> tuple[str, ...]:
    return (
        config.backend,
        config.temporal_address,
        config.temporal_namespace,
        config.temporal_task_queue,
        config.temporal_workflow_type,
        config.temporal_ui_url,
    )


__all__ = [
    "AuthorizedWorkflowBackend",
    "ConfiguredWorkflowBackendBridge",
    "DURABLE_RUN_SIGNAL_SCHEMA",
    "DURABLE_RUN_START_SCHEMA",
    "HubVerifiedDurableCommandPort",
    "InMemoryWorkflowControlBindingStore",
    "ROUTE_CONTROL_AUTHORIZATION_SCHEMA",
    "UnavailableWorkflowRuntimeReleaseAdmission",
    "WorkflowBackendControlFacade",
    "WorkflowBackendDurableRunAdapter",
    "WorkflowControlBindingStore",
    "WorkflowControlBindingOwnerResolver",
    "WorkflowControlRunBinding",
    "WorkflowRuntimeReleaseAdmissionPort",
    "build_workflow_backend_control_facade",
    "get_workflow_backend_control_facade",
    "reset_workflow_backend_control_facade",
]
