"""Production dependency adapters for the Hub workflow-control facade."""

from __future__ import annotations

from pathlib import Path
from typing import cast

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


__all__ = [
    "production_authorization_grants",
    "production_binding_store",
    "production_command_key_ring",
    "production_command_replay_store",
    "production_dispatch_intent_store",
    "production_read_model_projector",
    "production_release_admission",
    "production_rollout_policies",
    "production_runtime_health",
    "production_runtime_profiles",
]
