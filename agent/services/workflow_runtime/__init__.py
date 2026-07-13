"""Framework-neutral contracts and persistence ports for Ananta workflows."""

from importlib import import_module

from agent.services.workflow_runtime.commands import (
    WORKFLOW_COMMAND_SCHEMA,
    WORKFLOW_COMMAND_TYPES,
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.components import (
    WORKFLOW_COMPONENT_SCHEMA,
    WorkflowComponent,
    WorkflowComponentCompiler,
    WorkflowComponentRegistry,
)
from agent.services.workflow_runtime.errors import (
    ContractIssue,
    ContractValidationError,
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
    SignatureValidationError,
    UnsupportedSchemaVersion,
    WorkflowRuntimeError,
)
from agent.services.workflow_runtime.events import (
    CANONICAL_WORKFLOW_EVENT_SCHEMA,
    CanonicalWorkflowEvent,
    EventStore,
    InMemoryEventStore,
    LegacyWorkflowBackendEventAdapter,
    WorkflowRunProjection,
)
from agent.services.workflow_runtime.execution_plan import (
    EXECUTION_PLAN_JSON_SCHEMA,
    EXECUTION_PLAN_SCHEMA,
    ArtifactContract,
    ExecutionBudget,
    ExecutionEdge,
    ExecutionGate,
    ExecutionNode,
    ExecutionPlan,
    WorkflowRequestExecutionPlanAdapter,
)
from agent.services.workflow_runtime.ownership import (
    ExecutionOwnership,
    ExecutionOwnershipStore,
    InMemoryExecutionOwnershipStore,
    OwnershipClaim,
    RetryBudgetOwner,
    RetryBudgetSnapshot,
    SQLiteExecutionOwnershipStore,
    ownership_event,
)
from agent.services.workflow_runtime.persistence import (
    CheckpointStore,
    InMemoryCheckpointStore,
    SQLiteCheckpointStore,
    SQLiteEventStore,
)
from agent.services.workflow_runtime.provider_budgets import (
    PROVIDER_BUDGET_RECEIPT_SCHEMA,
    InMemoryProviderBudgetStore,
    ProviderBudgetError,
    ProviderBudgetLimits,
    ProviderBudgetSnapshot,
    ProviderBudgetStore,
)
from agent.services.workflow_runtime.retention import (
    RETENTION_ATTESTATION_SCHEMA,
    RETENTION_POLICY_SCHEMA,
    WorkflowEventRetentionAttestation,
    WorkflowEventRetentionPolicy,
    WorkflowEventRetentionService,
)
from agent.services.workflow_runtime.schema_evolution import QuarantinedContract, UpcasterRegistry
from agent.services.workflow_runtime.security import (
    AUTHORIZATION_ENVELOPE_SCHEMA,
    SIGNED_CHECKPOINT_SCHEMA,
    WORKFLOW_STATE_SCHEMA,
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryReplayNonceStore,
    ReplayNonceStore,
    RuntimeAuthorizationEnvelope,
    SignedCheckpoint,
    WorkflowState,
)
from agent.services.workflow_runtime.side_effects import (
    InMemorySideEffectLedger,
    SideEffectClaim,
    SideEffectLedger,
    SideEffectRecord,
    SQLiteSideEffectLedger,
    operation_id_for,
    side_effect_event,
)

# Keep the contract package importable in dedicated Worker containers that do
# not ship or initialize Hub database models.  Hub-only SQLAlchemy adapters stay
# API-compatible through lazy attribute resolution.
_LAZY_PERSISTENCE_EXPORTS = {
    "RuntimeOutboxMessage": (
        "agent.services.workflow_runtime.sqlalchemy_event_stores",
        "RuntimeOutboxMessage",
    ),
    "SQLAlchemyCheckpointStore": (
        "agent.services.workflow_runtime.sqlalchemy_event_stores",
        "SQLAlchemyCheckpointStore",
    ),
    "SQLAlchemyEventStore": (
        "agent.services.workflow_runtime.sqlalchemy_event_stores",
        "SQLAlchemyEventStore",
    ),
    "SQLAlchemyRuntimeOutbox": (
        "agent.services.workflow_runtime.sqlalchemy_event_stores",
        "SQLAlchemyRuntimeOutbox",
    ),
    "SQLAlchemyExecutionOwnershipStore": (
        "agent.services.workflow_runtime.sqlalchemy_ownership",
        "SQLAlchemyExecutionOwnershipStore",
    ),
    "SQLAlchemyProviderBudgetStore": (
        "agent.services.workflow_runtime.sqlalchemy_provider_budgets",
        "SQLAlchemyProviderBudgetStore",
    ),
    "SQLAlchemySideEffectLedger": (
        "agent.services.workflow_runtime.sqlalchemy_side_effects",
        "SQLAlchemySideEffectLedger",
    ),
}


def __getattr__(name: str):
    target = _LAZY_PERSISTENCE_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = [
    "AUTHORIZATION_ENVELOPE_SCHEMA",
    "ArtifactContract",
    "AuthorizationVerifier",
    "CANONICAL_WORKFLOW_EVENT_SCHEMA",
    "CanonicalWorkflowEvent",
    "CheckpointStore",
    "ContractIssue",
    "ContractValidationError",
    "EXECUTION_PLAN_JSON_SCHEMA",
    "EXECUTION_PLAN_SCHEMA",
    "EventStore",
    "ExecutionBudget",
    "ExecutionEdge",
    "ExecutionGate",
    "ExecutionNode",
    "ExecutionOwnership",
    "ExecutionOwnershipStore",
    "ExecutionPlan",
    "FencingTokenError",
    "HmacKeyRing",
    "InMemoryCheckpointStore",
    "InMemoryEventStore",
    "InMemoryExecutionOwnershipStore",
    "InMemoryProviderBudgetStore",
    "InMemoryReplayNonceStore",
    "InMemorySideEffectLedger",
    "InvalidTransitionError",
    "LegacyWorkflowBackendEventAdapter",
    "OptimisticConcurrencyError",
    "OwnershipClaim",
    "PROVIDER_BUDGET_RECEIPT_SCHEMA",
    "ProviderBudgetError",
    "ProviderBudgetLimits",
    "ProviderBudgetSnapshot",
    "ProviderBudgetStore",
    "QuarantinedContract",
    "RETENTION_ATTESTATION_SCHEMA",
    "RETENTION_POLICY_SCHEMA",
    "ReplayNonceStore",
    "RetryBudgetOwner",
    "RetryBudgetSnapshot",
    "RuntimeOutboxMessage",
    "RuntimeAuthorizationEnvelope",
    "SIGNED_CHECKPOINT_SCHEMA",
    "SQLiteCheckpointStore",
    "SQLiteEventStore",
    "SQLiteExecutionOwnershipStore",
    "SQLiteSideEffectLedger",
    "SQLAlchemyCheckpointStore",
    "SQLAlchemyEventStore",
    "SQLAlchemyExecutionOwnershipStore",
    "SQLAlchemyProviderBudgetStore",
    "SQLAlchemyRuntimeOutbox",
    "SQLAlchemySideEffectLedger",
    "SideEffectClaim",
    "SideEffectLedger",
    "SideEffectRecord",
    "SignatureValidationError",
    "SignedCheckpoint",
    "UnsupportedSchemaVersion",
    "UpcasterRegistry",
    "WORKFLOW_STATE_SCHEMA",
    "WorkflowRequestExecutionPlanAdapter",
    "WorkflowEventRetentionAttestation",
    "WorkflowEventRetentionPolicy",
    "WorkflowEventRetentionService",
    "WorkflowRunProjection",
    "WorkflowRuntimeError",
    "WorkflowState",
    "WORKFLOW_COMMAND_SCHEMA",
    "WORKFLOW_COMMAND_TYPES",
    "WORKFLOW_COMPONENT_SCHEMA",
    "SignedWorkflowCommand",
    "WorkflowCommandVerifier",
    "WorkflowComponent",
    "WorkflowComponentCompiler",
    "WorkflowComponentRegistry",
    "operation_id_for",
    "ownership_event",
    "side_effect_event",
]
