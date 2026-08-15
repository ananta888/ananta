"""Production dependency adapters for the Hub workflow-control facade."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, cast

from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_backend import WorkflowBackend
from agent.services.workflow_control_bindings import WorkflowControlBindingStore
from agent.services.workflow_control_dispatch_intents import (
    WorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_control_release_selection import (
    UnavailableWorkflowRuntimeReleaseAdmission,
    WorkflowRuntimeReleaseAdmissionPort,
)
from agent.services.workflow_runtime.security import ReplayNonceStore, SignatureSigningKeyRingPort
from agent.services.workflow_runtime_rollout_service import (
    WorkflowRolloutPolicyService,
)
from agent.services.workflow_runtime_selection_service import (
    RuntimeHealthPort,
    WorkflowRuntimeProfileService,
)


def production_release_admission(
    backend: WorkflowBackend,
) -> WorkflowRuntimeReleaseAdmissionPort:
    try:
        from agent.services.workflow_runtime.release_gate import (
            WorkflowRuntimeReleaseAdmission,
            WorkflowRuntimeReleaseGate,
        )

        root = Path(__file__).resolve().parents[2]
        gate = WorkflowRuntimeReleaseGate()
        return cast(
            WorkflowRuntimeReleaseAdmissionPort,
            WorkflowRuntimeReleaseAdmission.from_file(
                root / "artifacts" / "test-gates" / "workflow-runtime-production-v1.json",
                expected_contract_hash=gate.contract_hash,
                runtime_aliases={
                    "ananta-native": "native",
                    "langgraph": "langgraph",
                    "temporal": "temporal",
                },
            ),
        )
    except (OSError, TypeError, ValueError):
        return UnavailableWorkflowRuntimeReleaseAdmission()


def production_runtime_health(backend: WorkflowBackend) -> RuntimeHealthPort:
    del backend
    from agent.services.workflow_runtime_health_service import (
        default_workflow_runtime_health_service,
    )

    return default_workflow_runtime_health_service()


def production_runtime_profiles() -> WorkflowRuntimeProfileService:
    from agent.services.workflow_runtime_selection_service import (
        default_workflow_runtime_profile_service,
    )

    return default_workflow_runtime_profile_service()


def production_rollout_policies() -> WorkflowRolloutPolicyService:
    from agent.database import engine
    from agent.services.workflow_runtime_rollout_persistence import (
        SQLAlchemyWorkflowRolloutPolicyStore,
    )

    return WorkflowRolloutPolicyService(SQLAlchemyWorkflowRolloutPolicyStore(engine))


def production_authorization_grants() -> WorkflowAuthorizationGrantPort:
    from agent.database import engine
    from agent.services.workflow_authorization_grant_service import (
        SQLAlchemyWorkflowAuthorizationGrantService,
    )

    return SQLAlchemyWorkflowAuthorizationGrantService(engine)


def production_command_key_ring(backend: WorkflowBackend) -> SignatureSigningKeyRingPort | None:
    # Production registers every runtime bridge, so persisted commands may be
    # selected for Native or Temporal even when LangGraph is the configured
    # primary. Never fall back to a process-local key in that composition.
    del backend
    from agent.services.workflow_hub_task_gateway_runtime import (
        get_workflow_authorization_key_ring,
    )

    return get_workflow_authorization_key_ring()


def production_binding_store() -> WorkflowControlBindingStore:
    from agent.database import engine
    from agent.services.workflow_control_persistence import (
        SQLAlchemyWorkflowControlBindingStore,
    )

    return SQLAlchemyWorkflowControlBindingStore(engine)


def production_command_replay_store() -> ReplayNonceStore:
    from agent.database import engine
    from agent.services.workflow_control_persistence import (
        SQLAlchemyWorkflowCommandReplayNonceStore,
    )

    return SQLAlchemyWorkflowCommandReplayNonceStore(engine)


def production_dispatch_intent_store() -> WorkflowControlDispatchIntentStore:
    from agent.database import engine
    from agent.services.workflow_control_dispatch_persistence import (
        SQLAlchemyWorkflowControlDispatchIntentStore,
    )

    return SQLAlchemyWorkflowControlDispatchIntentStore(engine)


def production_read_model_projector() -> WorkflowControlReadModelProjector:
    from agent.database import engine
    from agent.services.workflow_runtime.sqlalchemy_event_stores import (
        SQLAlchemyEventStore,
    )
    from agent.services.workflow_runtime_read_model_service import (
        get_workflow_runtime_read_model_service,
    )

    return WorkflowControlReadModelProjector(
        get_workflow_runtime_read_model_service(),
        event_store=SQLAlchemyEventStore(engine),
    )


ANANTA_TERMINAL_TRACE_ENV = "ANANTA_WORKFLOW_TERMINAL_TRACE"
ANANTA_COMMAND_TRANSITIONS_ENV = "ANANTA_WORKFLOW_COMMAND_TRANSITIONS"


def _enabled(name: str) -> bool:
    """Read one opt-in switch.

    Both paths default to off.  They are new behaviour on the most
    safety-critical path in the Hub, so a deployment must ask for them rather
    than inherit them from an upgrade.
    """

    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def production_terminal_trace_runtime() -> Any | None:
    """Build the durable terminal-trace path when it is switched on.

    This is the safe half of the transition cutover: it changes no command
    path and only makes a terminal run's final trace survive a failed
    projection.  That evidence is worth having in place before commands move.
    """

    if not _enabled(ANANTA_TERMINAL_TRACE_ENV):
        return None
    from agent.database import engine
    from agent.services.workflow_terminal_trace_reconciliation import (
        build_workflow_terminal_trace_runtime,
    )

    def _history(candidate: Any, cursor: str) -> Any:
        """Read the run's canonical events as plain mappings for paging."""

        from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyEventStore

        after = int(cursor) if cursor.isdigit() else 0
        return [
            event.to_dict()
            for event in SQLAlchemyEventStore(engine).list_events(
                tenant_id=candidate.tenant_id,
                run_id=candidate.run_id,
                after_sequence=after,
            )
        ]

    def _project(candidate: Any, events: tuple[dict[str, Any], ...]) -> None:
        """Project one page through the same read model the bridge already uses.

        The binding and its last status are read here rather than carried on
        the candidate, so the projection always reflects what the Hub holds now
        instead of what was true when the trace was marked pending.
        """

        bindings = production_binding_store()
        binding = bindings.get(candidate.workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        status = bindings.last_status(candidate.workflow_id) or {}
        production_read_model_projector().project(
            binding=binding,
            status=dict(status),
            runtime=str(binding.runtime_id),
            mode="live",
            events=events,
        )

    return build_workflow_terminal_trace_runtime(engine, history=_history, project=_project)


def production_command_transition_runtime(status_reads: Any) -> Any | None:
    """Build the Native command-transition path when it is switched on."""

    if not _enabled(ANANTA_COMMAND_TRANSITIONS_ENV):
        return None
    from agent.database import engine
    from agent.services.workflow_transition_native_composition import (
        build_native_command_transition_runtime,
    )

    return build_native_command_transition_runtime(
        engine,
        status_reads=status_reads,
        owner_id=f"hub-transition-runner:{uuid.uuid4().hex}",
    )


__all__ = [
    "production_authorization_grants",
    "production_binding_store",
    "production_command_key_ring",
    "production_command_replay_store",
    "production_command_transition_runtime",
    "production_dispatch_intent_store",
    "production_read_model_projector",
    "production_release_admission",
    "production_rollout_policies",
    "production_terminal_trace_runtime",
    "production_runtime_health",
    "production_runtime_profiles",
]
