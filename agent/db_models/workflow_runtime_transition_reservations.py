"""Transition-owned effect proof rows.

Side-effect authorizations, execution-ownership reservations, queue
reservations and checkpoint bindings staged by workflow transitions.

Re-exported by :mod:`agent.db_models.workflow_runtime`, which imports these
modules in their original definition order so table registration on
``SQLModel.metadata`` is unchanged.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel


class WorkflowTransitionSideEffectAuthorizationDB(SQLModel, table=True):
    """Append-only proof of one transition-owned ledger authorization."""

    __tablename__ = "workflow_transition_side_effect_authorizations"
    __table_args__ = (
        sa.UniqueConstraint(
            "effect_id",
            name="uq_workflow_transition_side_effect_auth_effect",
        ),
        sa.UniqueConstraint(
            "operation_fence_id",
            name="uq_workflow_transition_side_effect_auth_fence",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "authorized_ledger_revision",
            name="uq_workflow_transition_side_effect_auth_revision",
        ),
        sa.CheckConstraint(
            "ownership_fencing_token > 0 AND creator_claim_generation > 0 AND authorized_ledger_revision > 1",
            name="ck_workflow_transition_side_effect_auth_positive",
        ),
        sa.Index(
            "ix_workflow_transition_side_effect_auth_operation",
            "operation_id",
        ),
        sa.Index(
            "ix_workflow_transition_side_effect_auth_tenant_run",
            "tenant_id",
            "run_id",
        ),
        sa.Index(
            "ix_workflow_transition_side_effect_auth_transition",
            "transition_id",
        ),
    )

    receipt_id: str = Field(primary_key=True, max_length=256)
    transition_id: str = Field(max_length=256)
    effect_id: str = Field(max_length=256)
    operation_id: str = Field(max_length=256)
    operation_fence_id: str = Field(max_length=256)
    tenant_id: str = Field(max_length=256)
    workflow_id: str = Field(max_length=256)
    run_id: str = Field(max_length=256)
    runtime_id: str = Field(max_length=64)
    step_id: str = Field(max_length=256)
    operation_intent_digest: str = Field(max_length=64)
    authorization_envelope_id: str = Field(max_length=256)
    authorization_envelope_digest: str = Field(max_length=64)
    ownership_attempt_id: str = Field(max_length=256)
    ownership_fencing_token: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    creator_claim_generation: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    authorized_ledger_revision: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    planned_at: float
    authorized_at: float
    receipt_digest: str = Field(max_length=64)
    receipt: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowTransitionOwnershipReservationDB(SQLModel, table=True):
    """Append-only proof of one transition-owned execution reservation."""

    __tablename__ = "workflow_transition_ownership_reservations"
    __table_args__ = (
        sa.UniqueConstraint(
            "effect_id",
            name="uq_workflow_transition_ownership_res_effect",
        ),
        sa.UniqueConstraint(
            "operation_fence_id",
            name="uq_workflow_transition_ownership_res_fence",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_workflow_transition_ownership_res_attempt",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "acquired_revision",
            name="uq_workflow_transition_ownership_res_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "acquired_fencing_token",
            name="uq_workflow_transition_ownership_res_current_fence",
        ),
        sa.CheckConstraint(
            "creator_claim_generation > 0 "
            "AND acquired_revision > 0 "
            "AND acquired_revision <= 2147483647 "
            "AND acquired_fencing_token > 0 "
            "AND acquired_fencing_token <= 2147483647 "
            "AND maximum_retries >= 0 "
            "AND maximum_retries <= 2147483647 "
            "AND (retry_consumed = FALSE OR retry_consumed = TRUE) "
            "AND planned_at > 0 "
            "AND reserved_at >= planned_at "
            "AND lease_expires_at > reserved_at",
            name="ck_workflow_transition_ownership_res_valid",
        ),
        sa.Index(
            "ix_workflow_transition_ownership_res_transition",
            "transition_id",
        ),
        sa.Index(
            "ix_workflow_transition_ownership_res_tenant_run",
            "tenant_id",
            "run_id",
        ),
        sa.Index(
            "ix_workflow_transition_ownership_res_scope",
            "tenant_id",
            "run_id",
            "step_id",
        ),
        sa.Index(
            "ix_workflow_transition_ownership_res_owner",
            "owner_id",
        ),
    )

    receipt_id: str = Field(primary_key=True, max_length=256)
    transition_id: str = Field(max_length=256)
    effect_id: str = Field(max_length=256)
    operation_fence_id: str = Field(max_length=256)
    attempt_id: str = Field(max_length=256)
    owner_id: str = Field(max_length=256)
    tenant_id: str = Field(max_length=256)
    workflow_id: str = Field(max_length=256)
    run_id: str = Field(max_length=256)
    runtime_id: str = Field(max_length=64)
    step_id: str = Field(max_length=256)
    ownership_intent_digest: str = Field(max_length=64)
    acquisition_record_digest: str = Field(max_length=64)
    receipt_digest: str = Field(max_length=64)
    creator_claim_generation: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    acquired_revision: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    acquired_fencing_token: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    maximum_retries: int = Field(sa_column=Column(sa.Integer, nullable=False))
    retry_consumed: bool = Field(sa_column=Column(sa.Boolean, nullable=False))
    planned_at: float
    reserved_at: float
    lease_expires_at: float
    receipt: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowTransitionQueueReservationDB(SQLModel, table=True):
    """Append-only proof of one transition-owned task-queue reservation.

    The reservation is deliberately its own record rather than an attribution
    written into the task table.  Tasks are also created outside transitions,
    so a shared row would put two writers with different fencing rules on one
    piece of state; here the queue service stays the sole owner of tasks and
    this table only proves which transition effect was allowed to claim one.
    """

    __tablename__ = "workflow_transition_queue_reservations"
    __table_args__ = (
        sa.UniqueConstraint(
            "effect_id",
            name="uq_workflow_transition_queue_res_effect",
        ),
        sa.UniqueConstraint(
            "operation_fence_id",
            name="uq_workflow_transition_queue_res_fence",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_workflow_transition_queue_res_attempt",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "task_id",
            name="uq_workflow_transition_queue_res_task",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "reserved_revision",
            name="uq_workflow_transition_queue_res_revision",
        ),
        sa.CheckConstraint(
            "creator_claim_generation > 0 "
            "AND reserved_revision > 0 "
            "AND reserved_revision <= 2147483647 "
            "AND maximum_retries >= 0 "
            "AND maximum_retries <= 2147483647 "
            "AND (retry_consumed = FALSE OR retry_consumed = TRUE) "
            "AND planned_at > 0 "
            "AND reserved_at >= planned_at",
            name="ck_workflow_transition_queue_res_valid",
        ),
        sa.Index(
            "ix_workflow_transition_queue_res_transition",
            "transition_id",
        ),
        sa.Index(
            "ix_workflow_transition_queue_res_tenant_run",
            "tenant_id",
            "run_id",
        ),
        sa.Index(
            "ix_workflow_transition_queue_res_scope",
            "tenant_id",
            "run_id",
            "step_id",
        ),
        sa.Index(
            "ix_workflow_transition_queue_res_task",
            "task_id",
        ),
    )

    receipt_id: str = Field(primary_key=True, max_length=256)
    transition_id: str = Field(max_length=256)
    effect_id: str = Field(max_length=256)
    operation_fence_id: str = Field(max_length=256)
    attempt_id: str = Field(max_length=256)
    task_id: str = Field(max_length=256)
    tenant_id: str = Field(max_length=256)
    workflow_id: str = Field(max_length=256)
    run_id: str = Field(max_length=256)
    runtime_id: str = Field(max_length=64)
    step_id: str = Field(max_length=256)
    queue_intent_digest: str = Field(max_length=64)
    reservation_record_digest: str = Field(max_length=64)
    receipt_digest: str = Field(max_length=64)
    creator_claim_generation: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    reserved_revision: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    maximum_retries: int = Field(sa_column=Column(sa.Integer, nullable=False))
    retry_consumed: bool = Field(sa_column=Column(sa.Boolean, nullable=False))
    planned_at: float
    reserved_at: float
    receipt: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))


class WorkflowTransitionCheckpointBindingDB(SQLModel, table=True):
    """Append-only proof binding one transition effect to one checkpoint.

    The transition does not author checkpoint state; the runtime does.  This
    record only proves which exact checkpoint revision a transition effect was
    bound to, so a restart re-adopts that binding instead of binding the run
    to whatever revision happens to be current later.
    """

    __tablename__ = "workflow_transition_checkpoint_bindings"
    __table_args__ = (
        sa.UniqueConstraint(
            "effect_id",
            name="uq_workflow_transition_checkpoint_bind_effect",
        ),
        sa.UniqueConstraint(
            "operation_fence_id",
            name="uq_workflow_transition_checkpoint_bind_fence",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_workflow_transition_checkpoint_bind_attempt",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "task_id",
            "bound_revision",
            name="uq_workflow_transition_checkpoint_bind_revision",
        ),
        sa.CheckConstraint(
            "creator_claim_generation > 0 "
            "AND bound_revision > 0 "
            "AND bound_revision <= 2147483647 "
            "AND bound_fencing_token > 0 "
            "AND bound_fencing_token <= 2147483647 "
            "AND planned_at > 0 "
            "AND bound_at >= planned_at",
            name="ck_workflow_transition_checkpoint_bind_valid",
        ),
        sa.Index(
            "ix_workflow_transition_checkpoint_bind_transition",
            "transition_id",
        ),
        sa.Index(
            "ix_workflow_transition_checkpoint_bind_tenant_run",
            "tenant_id",
            "run_id",
        ),
        sa.Index(
            "ix_workflow_transition_checkpoint_bind_checkpoint",
            "checkpoint_id",
        ),
    )

    receipt_id: str = Field(primary_key=True, max_length=256)
    transition_id: str = Field(max_length=256)
    effect_id: str = Field(max_length=256)
    operation_fence_id: str = Field(max_length=256)
    attempt_id: str = Field(max_length=256)
    checkpoint_id: str = Field(max_length=256)
    task_id: str = Field(max_length=256)
    tenant_id: str = Field(max_length=256)
    workflow_id: str = Field(max_length=256)
    run_id: str = Field(max_length=256)
    runtime_id: str = Field(max_length=64)
    step_id: str = Field(max_length=256)
    checkpoint_intent_digest: str = Field(max_length=64)
    checkpoint_digest: str = Field(max_length=64)
    receipt_digest: str = Field(max_length=64)
    creator_claim_generation: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    bound_revision: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    bound_fencing_token: int = Field(sa_column=Column(sa.BigInteger, nullable=False))
    planned_at: float
    bound_at: float
    receipt: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
