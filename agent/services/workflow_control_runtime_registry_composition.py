"""Compose every production runtime behind one immutable Hub bridge registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.services.workflow_adapter_task_queue_composition import (
    build_workflow_adapter_task_queue_service,
)
from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_backend import WorkflowBackend
from agent.services.workflow_backend_durable_run_adapter import (
    WorkflowBackendDurableRunAdapter,
)
from agent.services.workflow_control_bindings import WorkflowControlBindingStore
from agent.services.workflow_control_command_verification import (
    HubSignedWorkflowCommandVerifier,
)
from agent.services.workflow_control_dispatch_intents import (
    WorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_provider_selection_service import (
    build_workflow_provider_decision_service,
)
from agent.services.workflow_runtime.commands import (
    WorkflowCommandIssuer,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.security import HmacKeyRing, ReplayNonceStore
from agent.services.workflow_runtime_bridge_registry import (
    WorkflowRuntimeBridgeRegistry,
)


def register_production_runtime_bridges(
    *,
    registry: WorkflowRuntimeBridgeRegistry,
    configured_bridge: Any,
    temporal_backend: WorkflowBackend,
    configured_bridge_factory: Callable[..., Any],
    bindings: WorkflowControlBindingStore,
    key_ring: HmacKeyRing,
    replay_store: ReplayNonceStore,
    authorization_grants: WorkflowAuthorizationGrantPort,
    read_models: WorkflowControlReadModelProjector | None,
    dispatch_intents: WorkflowControlDispatchIntentStore | None,
) -> None:
    """Register Native, LangGraph and Temporal; absence fails at composition."""

    registry.register(
        configured_bridge.selection_runtime_id,
        configured_bridge,
        aliases=(configured_bridge.runtime_id,),
    )
    if configured_bridge.selection_runtime_id != "ananta-native":
        from agent.database import engine
        from agent.services.native_graph_production_composition import (
            build_native_graph_workflow_control_bridge,
        )

        native = build_native_graph_workflow_control_bridge(
            engine=engine,
            bindings=bindings,
            key_ring=key_ring,
            replay_store=replay_store,
            authorization_grants=authorization_grants,
            read_models=read_models,
        )
        registry.register("ananta-native", native, aliases=("local",))
    if configured_bridge.selection_runtime_id != "temporal":
        if temporal_backend.backend_id != "temporal":
            raise ValueError("temporal_runtime_bridge_required")
        commands = HubSignedWorkflowCommandVerifier(WorkflowCommandVerifier(key_ring, replay_store))
        temporal = configured_bridge_factory(
            temporal_backend,
            bindings,
            durable_runs=WorkflowBackendDurableRunAdapter(
                temporal_backend,
                commands=commands,
                command_issuer=WorkflowCommandIssuer(key_ring),
            ),
            commands=commands,
            read_models=read_models,
            authorization_grants=authorization_grants,
            dispatch_intents=dispatch_intents,
        )
        registry.register("temporal", temporal)

    from agent.database import engine
    from agent.services.langgraph_workflow_control_bridge import (
        LangGraphWorkflowControlBridge,
    )
    from agent.services.workflow_runtime_capacity_service import (
        CapacityGuardedWorkflowAdapterQueue,
        SQLAlchemyWorkflowRuntimeCapacity,
    )

    capacity = SQLAlchemyWorkflowRuntimeCapacity(engine)
    langgraph = LangGraphWorkflowControlBridge(
        queue=CapacityGuardedWorkflowAdapterQueue(
            build_workflow_adapter_task_queue_service(),
            capacity,
        ),
        bindings=bindings,
        command_verifier=WorkflowCommandVerifier(key_ring, replay_store),
        provider_decisions=build_workflow_provider_decision_service(),
        read_models=read_models,
        capacity=capacity,
    )
    registry.register("langgraph", langgraph)


__all__ = ["register_production_runtime_bridges"]
