"""Production composition root for the Hub-owned Native graph runtime."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from agent.services.native_graph_control_bridge import (
    NativeGraphWorkflowControlBridge,
)
from agent.services.native_graph_models import NativeRunState
from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
from agent.services.native_graph_task_queue_adapter import (
    build_native_graph_task_queue_adapter,
)
from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_control_bindings import WorkflowControlBindingStore
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_provider_selection_service import (
    WorkflowProviderDecisionPort,
    build_workflow_provider_decision_service,
)
from agent.services.workflow_runtime.commands import (
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.components import (
    WorkflowComponentCompiler,
    WorkflowComponentRegistry,
)
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.security import HmacKeyRing, ReplayNonceStore
from agent.services.workflow_runtime.sqlalchemy_event_stores import (
    SQLAlchemyCheckpointStore,
    SQLAlchemyEventStore,
)
from agent.services.workflow_runtime.sqlalchemy_ownership import (
    SQLAlchemyExecutionOwnershipStore,
)
from agent.services.workflow_runtime.sqlalchemy_side_effects import (
    SQLAlchemySideEffectLedger,
)


class HubGovernedNativeControlPolicy:
    """Small policy adapter after Hub auth, selection, and signed decisions."""

    _COMMANDS = frozenset(
        {
            "approve",
            "reject",
            "edit",
            "request_changes",
            "pause",
            "resume",
            "retry",
            "cancel",
            "parameter_update",
        }
    )

    def authorize_command(
        self,
        command: SignedWorkflowCommand,
        *,
        plan: ExecutionPlan,
        state: NativeRunState,
    ) -> tuple[bool, str]:
        del plan
        if command.command_type not in self._COMMANDS:
            return False, "native_control_command_not_admitted"
        if not command.actor_id:
            return False, "native_control_actor_required"
        if state.status == "completed":
            return False, "native_control_run_completed"
        return True, "native_control_hub_decision_admitted"

    def authorize_delegation(
        self,
        *,
        plan: ExecutionPlan,
        node: ExecutionNode,
        state: NativeRunState,
    ) -> tuple[bool, str]:
        if state.status != "running":
            return False, "native_delegation_run_not_running"
        if node.node_id not in {candidate.node_id for candidate in plan.nodes}:
            return False, "native_delegation_node_not_in_plan"
        if set(node.required_capabilities) - set(plan.capabilities):
            return False, "native_delegation_capability_not_declared"
        return True, "native_delegation_hub_policy_admitted"


def build_native_graph_workflow_control_bridge(
    *,
    engine: Engine,
    bindings: WorkflowControlBindingStore,
    key_ring: HmacKeyRing,
    replay_store: ReplayNonceStore,
    authorization_grants: WorkflowAuthorizationGrantPort,
    read_models: WorkflowControlReadModelProjector | None = None,
    provider_decisions: WorkflowProviderDecisionPort | None = None,
    component_compiler: WorkflowComponentCompiler | None = None,
    queue: Any | None = None,
) -> NativeGraphWorkflowControlBridge:
    """Compose only persistent Hub stores and the real Hub task queue."""

    from agent.services.workflow_runtime.telemetry_runtime import (
        configure_workflow_telemetry,
    )

    events = configure_workflow_telemetry(SQLAlchemyEventStore(engine))
    orchestrator = NativeGraphOrchestrator(
        queue=queue or build_native_graph_task_queue_adapter(),
        checkpoints=SQLAlchemyCheckpointStore(engine),
        events=events,
        ownership=SQLAlchemyExecutionOwnershipStore(engine),
        ledger=SQLAlchemySideEffectLedger(engine),
        key_ring=key_ring,
        command_verifier=WorkflowCommandVerifier(key_ring, replay_store),
        policy=HubGovernedNativeControlPolicy(),
        authorization_grants=authorization_grants,
        component_compiler=(component_compiler or WorkflowComponentCompiler(WorkflowComponentRegistry())),
        provider_decisions=(provider_decisions or build_workflow_provider_decision_service()),
    )
    return NativeGraphWorkflowControlBridge(
        orchestrator=orchestrator,
        bindings=bindings,
        read_models=read_models,
    )


__all__ = [
    "HubGovernedNativeControlPolicy",
    "build_native_graph_workflow_control_bridge",
]
