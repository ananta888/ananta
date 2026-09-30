"""Hub-owned persistence models for the durable workflow runtime.

The models deliberately contain only persistence concerns.  Runtime state
machines and validation remain in :mod:`agent.services.workflow_runtime`, so
SQLite and PostgreSQL use exactly the same domain contracts.

The models are split by aggregate into ``workflow_runtime_*`` sibling modules;
this module re-exports them (importing them in the original definition order)
and keeps the command-nonce, capacity, read-model, rollout and authorization
grant rows.
"""

from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

# isort: off
# Aggregates are imported in their original definition order so that
# SQLModel.metadata registers the tables in the same order as before the split.
from agent.db_models.workflow_runtime_state import (  # noqa: F401 - public re-export
    WorkflowRuntimeEventDB,
    WorkflowRuntimeCheckpointDB,
    WorkflowSideEffectLedgerDB,
)
from agent.db_models.workflow_runtime_transition_reservations import (  # noqa: F401 - public re-export
    WorkflowTransitionSideEffectAuthorizationDB,
    WorkflowTransitionOwnershipReservationDB,
    WorkflowTransitionQueueReservationDB,
    WorkflowTransitionCheckpointBindingDB,
)
from agent.db_models.workflow_runtime_execution import (  # noqa: F401 - public re-export
    WorkflowExecutionOwnershipDB,
    WorkflowExecutionAttemptHistoryDB,
    WorkflowWorkerAssignmentDB,
    WorkflowRetryBudgetDB,
    WorkflowRetryConsumptionDB,
    WorkflowProviderBudgetDB,
    WorkflowProviderBudgetReservationDB,
)
from agent.db_models.workflow_runtime_control import (  # noqa: F401 - public re-export
    WorkflowRuntimeOutboxDB,
    WorkflowControlBindingDB,
    WorkflowControlDispatchIntentDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionOutboxDB,
    WorkflowTransitionEffectDB,
)
# isort: on


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
