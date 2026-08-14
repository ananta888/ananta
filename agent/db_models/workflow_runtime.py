"""Hub-owned persistence models for the durable workflow runtime.

The models deliberately contain only persistence concerns.  Runtime state
machines and validation remain in :mod:`agent.services.workflow_runtime`, so
SQLite and PostgreSQL use exactly the same domain contracts.
"""

from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel


class WorkflowRuntimeEventDB(SQLModel, table=True):
    """Append-only canonical event row."""

    __tablename__ = "workflow_runtime_events"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "run_id", "sequence", name="uq_workflow_runtime_event_sequence"),
        sa.UniqueConstraint("tenant_id", "run_id", "dedupe_key", name="uq_workflow_runtime_event_dedupe"),
        sa.UniqueConstraint("tenant_id", "run_id", "event_id", name="uq_workflow_runtime_event_id"),
        sa.Index(
            "ix_workflow_runtime_events_tenant_run_sequence",
            "tenant_id",
            "run_id",
            "sequence",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    sequence: int
    event_id: str
    event_type: str = Field(index=True)
    dedupe_key: str
    content_hash: str
    occurred_at: float = Field(index=True)
    canonical_event: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowRuntimeCheckpointDB(SQLModel, table=True):
    """Immutable checkpoint revision; latest state is derived by revision."""

    __tablename__ = "workflow_runtime_checkpoints"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "checkpoint_id", name="uq_workflow_runtime_checkpoint_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "task_id",
            "revision",
            name="uq_workflow_runtime_checkpoint_revision",
        ),
        sa.Index(
            "ix_workflow_runtime_checkpoints_latest",
            "tenant_id",
            "run_id",
            "task_id",
            "revision",
        ),
    )

    id: str = Field(primary_key=True)
    checkpoint_id: str
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    task_id: str = Field(index=True)
    revision: int
    fencing_token: int
    created_at: float = Field(index=True)
    signed_checkpoint: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowSideEffectLedgerDB(SQLModel, table=True):
    """Current state of one stable, tenant-bound external operation."""

    __tablename__ = "workflow_side_effect_ledger"
    __table_args__ = (
        sa.Index(
            "ix_workflow_side_effect_ledger_tenant_run",
            "tenant_id",
            "run_id",
        ),
    )

    operation_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    status: str = Field(index=True)
    revision: int
    fencing_token: int
    updated_at: float = Field(index=True)
    record: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowExecutionOwnershipDB(SQLModel, table=True):
    """CAS-protected current owner of a hub-delegated workflow step."""

    __tablename__ = "workflow_execution_ownership"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            name="uq_workflow_execution_ownership_step",
        ),
        sa.Index(
            "ix_workflow_execution_ownership_lease",
            "tenant_id",
            "status",
            "lease_expires_at",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    attempt_id: str = Field(index=True)
    owner_id: str = Field(index=True)
    status: str = Field(index=True)
    revision: int
    fencing_token: int
    lease_expires_at: float = Field(index=True)
    last_heartbeat_at: float
    ownership: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowExecutionAttemptHistoryDB(SQLModel, table=True):
    """Immutable audit history for every ownership revision."""

    __tablename__ = "workflow_execution_attempt_history"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "revision",
            name="uq_workflow_execution_attempt_revision",
        ),
        sa.Index(
            "ix_workflow_execution_attempt_history_run",
            "tenant_id",
            "run_id",
            "step_id",
            "revision",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    attempt_id: str = Field(index=True)
    owner_id: str = Field(index=True)
    status: str = Field(index=True)
    revision: int
    fencing_token: int
    recorded_at: float = Field(index=True)
    ownership: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowWorkerAssignmentDB(SQLModel, table=True):
    """Hub-issued binding from one fenced lease to one registered Worker."""

    __tablename__ = "workflow_worker_assignments"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            name="uq_workflow_worker_assignment_step",
        ),
        sa.Index(
            "ix_workflow_worker_assignment_worker",
            "worker_id",
            "worker_url",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    attempt_id: str = Field(index=True)
    fencing_token: int
    hub_task_id: str = Field(index=True)
    worker_id: str = Field(index=True)
    worker_url: str
    revision: int = 1
    assigned_at: float = Field(index=True)


class WorkflowRetryBudgetDB(SQLModel, table=True):
    """One combined retry counter for all runtime layers of a run."""

    __tablename__ = "workflow_retry_budgets"
    __table_args__ = (sa.UniqueConstraint("tenant_id", "run_id", name="uq_workflow_retry_budget_run"),)

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    used: int = 0
    maximum: int
    revision: int = 1
    updated_at: float = Field(index=True)


class WorkflowRetryConsumptionDB(SQLModel, table=True):
    """Dedupe record preventing retry multiplication across runtimes."""

    __tablename__ = "workflow_retry_consumptions"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "run_id", "retry_id", name="uq_workflow_retry_consumption_id"),
        sa.Index(
            "ix_workflow_retry_consumptions_run",
            "tenant_id",
            "run_id",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    retry_id: str
    category: str = Field(index=True)
    consumed_at: float = Field(index=True)


class WorkflowProviderBudgetDB(SQLModel, table=True):
    """CAS-protected aggregate budget shared by every worker of one run."""

    __tablename__ = "workflow_provider_budgets"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "policy_version",
            name="uq_workflow_provider_budget_binding",
        ),
        sa.Index(
            "ix_workflow_provider_budgets_tenant_run",
            "tenant_id",
            "run_id",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    policy_version: str = Field(index=True)
    attempts: int = 0
    tokens: int = 0
    cost_micros: int = 0
    maximum_attempts: int
    maximum_tokens: int
    maximum_cost_micros: int
    revision: int = 1
    updated_at: float = Field(index=True)


class WorkflowProviderBudgetReservationDB(SQLModel, table=True):
    """Idempotent reservation/reconciliation record for one provider call."""

    __tablename__ = "workflow_provider_budget_reservations"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "reservation_id",
            name="uq_workflow_provider_budget_reservation",
        ),
        sa.Index(
            "ix_workflow_provider_budget_reservations_budget",
            "budget_id",
            "created_at",
        ),
    )

    id: str = Field(primary_key=True)
    budget_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    policy_version: str = Field(index=True)
    reservation_id: str
    reserved_tokens: int
    reserved_cost_micros: int
    actual_total_tokens: Optional[int] = None
    reconciled: bool = Field(default=False, index=True)
    created_at: float = Field(index=True)
    updated_at: float = Field(index=True)


class WorkflowRuntimeOutboxDB(SQLModel, table=True):
    """Transactional outbox entry for committed canonical events."""

    __tablename__ = "workflow_runtime_outbox"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "topic", "dedupe_key", name="uq_workflow_runtime_outbox_dedupe"),
        sa.Index(
            "ix_workflow_runtime_outbox_delivery",
            "tenant_id",
            "status",
            "available_at",
            "created_at",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    aggregate_id: str = Field(index=True)
    topic: str = Field(index=True)
    dedupe_key: str
    status: str = Field(default="pending", index=True)
    revision: int = 1
    attempts: int = 0
    available_at: float = Field(index=True)
    claimed_by: str = ""
    claim_expires_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(index=True)
    published_at: Optional[float] = Field(default=None, index=True)
    payload: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowControlBindingDB(SQLModel, table=True):
    """Restart-safe Hub ownership and legacy workflow-control binding."""

    __tablename__ = "workflow_control_bindings"
    __table_args__ = (
        sa.UniqueConstraint("workflow_id", name="uq_workflow_control_binding_workflow"),
        sa.UniqueConstraint("run_id", name="uq_workflow_control_binding_run"),
        sa.Index(
            "ix_workflow_control_bindings_owner",
            "tenant_id",
            "subject_id",
            "workflow_id",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    subject_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    runtime_id: str = Field(index=True)
    plan_hash: str
    policy_version: str
    checkpoint_id: str
    workflow_request: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
    execution_plan: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    last_status: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    public_status: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    runtime_revision: int = 0
    runtime_checkpoint_ref: str
    command_claim: str = Field(default="", index=True)
    command_claim_expires_at: float = Field(default=0.0, index=True)
    command_observation_pending: bool = Field(default=False, index=True)
    command_observation_min_revision: int = 0
    command_observation_expected_status: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    dispatch_intent_id: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default="", index=True),
    )
    command_receipt_id: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default="", index=True),
    )
    scheduler_owner: str = Field(default="", index=True)
    scheduler_lease_expires_at: float = Field(default=0.0, index=True)
    revision: int = 1
    created_at: float = Field(index=True)
    updated_at: float = Field(index=True)


class WorkflowControlDispatchIntentDB(SQLModel, table=True):
    """Hub-owned, leased intent for restart-safe Temporal mutations."""

    __tablename__ = "workflow_control_dispatch_intents"
    __table_args__ = (
        sa.Index(
            "ix_workflow_control_dispatch_due",
            "state",
            "available_at",
            "lease_expires_at",
        ),
        sa.Index(
            "ix_workflow_control_dispatch_workflow",
            "workflow_id",
            "state",
        ),
    )

    id: str = Field(sa_column=Column(sa.String(256), primary_key=True))
    kind: str = Field(sa_column=Column(sa.String(32), nullable=False, index=True))
    tenant_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    workflow_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    run_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    payload: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
    state: str = Field(sa_column=Column(sa.String(32), nullable=False, index=True))
    dispatch_from_state: str = Field(
        default="ready",
        sa_column=Column(sa.String(32), nullable=False, server_default="ready"),
    )
    acknowledgement_revision: int = 0
    acknowledgement_status: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    attempt_count: int = 0
    available_at: float = Field(index=True)
    lease_owner: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default="", index=True),
    )
    lease_expires_at: float = Field(default=0.0, index=True)
    last_error: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default=""),
    )
    revision: int = 1
    created_at: float = Field(index=True)
    updated_at: float = Field(index=True)


class WorkflowControlCommandReceiptDB(SQLModel, table=True):
    """Hub-owned idempotency receipt for non-Temporal control commands."""

    __tablename__ = "workflow_control_command_receipts"
    __table_args__ = (
        sa.Index(
            "ix_workflow_control_command_receipts_workflow_state",
            "workflow_id",
            "state",
        ),
    )

    id: str = Field(sa_column=Column(sa.String(256), primary_key=True))
    tenant_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    workflow_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    run_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    actor_id: str = Field(sa_column=Column(sa.String(256), nullable=False, index=True))
    command_type: str = Field(sa_column=Column(sa.String(64), nullable=False, index=True))
    request_payload: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
    expected_revision: int
    checkpoint_ref: str = Field(sa_column=Column(sa.String(512), nullable=False))
    state: str = Field(sa_column=Column(sa.String(32), nullable=False, index=True))
    result_status: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False),
    )
    rejection_reason: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    dispatch_owner: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default="", index=True),
    )
    dispatch_lease_expires_at: float = Field(default=0.0, index=True)
    revision: int = 1
    created_at: float = Field(index=True)
    updated_at: float = Field(index=True)


class WorkflowCommandNonceDB(SQLModel, table=True):
    """Hashed, tenant-bound nonce consumed by the Hub command verifier."""

    __tablename__ = "workflow_command_nonces"

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    nonce_hash: str
    expires_at: float = Field(index=True)
    consumed_at: float = Field(index=True)


class WorkflowRuntimeCapacityLockDB(SQLModel, table=True):
    """Single durable row serializing cross-run capacity reservations."""

    __tablename__ = "workflow_runtime_capacity_lock"

    id: str = Field(primary_key=True)
    revision: int = 0
    updated_at: float = 0.0


class WorkflowRuntimeCapacityReservationDB(SQLModel, table=True):
    """Idempotent active slot held by one Hub-delegated runtime task."""

    __tablename__ = "workflow_runtime_capacity_reservations"
    __table_args__ = (
        sa.Index(
            "ix_workflow_runtime_capacity_active_tenant",
            "active",
            "tenant_id",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    hub_task_id: str = Field(default="", index=True)
    active: bool = Field(default=True, index=True)
    created_at: float = Field(index=True)
    released_at: float = Field(default=0.0, index=True)


class WorkflowRuntimeReadModelDB(SQLModel, table=True):
    """Tenant-scoped durable projection consumed by the operations UI."""

    __tablename__ = "workflow_runtime_read_models"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            name="uq_workflow_runtime_read_model_run",
        ),
        sa.Index(
            "ix_workflow_runtime_read_models_tenant_updated",
            "tenant_id",
            "updated_at",
        ),
    )

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    runtime: str = Field(index=True)
    mode: str = Field(index=True)
    status: str = Field(index=True)
    source_sequence: int
    updated_at: float = Field(index=True)
    record: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowRuntimeRolloutPolicyDB(SQLModel, table=True):
    """Current CAS-protected policy for one Hub rollout scope."""

    __tablename__ = "workflow_runtime_rollout_policies"
    __table_args__ = (
        sa.Index(
            "ix_workflow_runtime_rollout_scope",
            "project_id",
            "tenant_id",
            "profile_id",
            "workflow_id",
        ),
    )

    id: str = Field(primary_key=True)
    scope_type: str = Field(index=True)
    project_id: str = Field(index=True)
    tenant_id: str = Field(default="", index=True)
    profile_id: str = Field(default="", index=True)
    workflow_id: str = Field(default="", index=True)
    policy_version: str = Field(index=True)
    mode: str = Field(index=True)
    revision: int = 1
    created_at: float = Field(index=True)
    updated_at: float = Field(index=True)
    policy: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowRuntimeRolloutAuditDB(SQLModel, table=True):
    """Immutable rollout, rollback, shadow and incident audit record."""

    __tablename__ = "workflow_runtime_rollout_audit"
    __table_args__ = (
        sa.Index(
            "ix_workflow_runtime_rollout_audit_scope_time",
            "scope_key",
            "occurred_at",
        ),
    )

    id: str = Field(primary_key=True)
    scope_key: str = Field(index=True)
    scope_type: str = Field(index=True)
    project_id: str = Field(index=True)
    tenant_id: str = Field(default="", index=True)
    profile_id: str = Field(default="", index=True)
    workflow_id: str = Field(default="", index=True)
    action: str = Field(index=True)
    actor_id: str = Field(index=True)
    reason_code: str = Field(index=True)
    occurred_at: float = Field(index=True)
    event: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowAuthorizationGrantDB(SQLModel, table=True):
    """Current persistent Hub grant for one signed runtime envelope."""

    __tablename__ = "workflow_authorization_grants"
    __table_args__ = (
        sa.Index(
            "ix_workflow_authorization_grants_binding",
            "tenant_id",
            "run_id",
            "step_id",
            "status",
        ),
    )

    envelope_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_id: str = Field(index=True)
    run_id: str = Field(index=True)
    step_id: str = Field(index=True)
    plan_hash: str = Field(index=True)
    policy_version: str = Field(index=True)
    grant_digest: str
    status: str = Field(default="active", index=True)
    revision: int = 1
    issued_at: float = Field(index=True)
    expires_at: float = Field(index=True)
    updated_at: float = Field(index=True)
    revoked_at: Optional[float] = Field(default=None, index=True)
    revocation_reason: str = ""
