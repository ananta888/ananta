"""Execution ownership, attempt history, worker assignment and budget rows.

Re-exported by :mod:`agent.db_models.workflow_runtime`, which imports these
modules in their original definition order so table registration on
``SQLModel.metadata`` is unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel


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
