"""Workflow control-plane rows.

Runtime outbox, control bindings, dispatch intents, command receipts and the
transition outbox with its effects.

Re-exported by :mod:`agent.db_models.workflow_runtime`, which imports these
modules in their original definition order so table registration on
``SQLModel.metadata`` is unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel


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
    active_transition_id: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default="", index=True),
    )
    last_transition_id: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default=""),
    )
    last_transition_command_id: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default=""),
    )
    last_transition_request_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    last_transition_effect_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    last_transition_outcome_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    scheduler_owner: str = Field(default="", index=True)
    scheduler_lease_expires_at: float = Field(default=0.0, index=True)
    # Terminal trace projection is durable, not best effort: a run that reached
    # a terminal state stays pending until a projection acknowledges it, so a
    # restart resumes the reconciliation instead of losing the final trace.
    trace_pending: bool = Field(default=False, index=True)
    trace_pending_revision: int = Field(
        default=0,
        sa_column=Column(sa.Integer, nullable=False, server_default="0"),
    )
    trace_projected_revision: int = Field(
        default=0,
        sa_column=Column(sa.Integer, nullable=False, server_default="0"),
    )
    trace_cursor: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default=""),
    )
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
    request_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    transition_id: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default="", index=True),
    )
    effect_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    outcome_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    dispatch_generation: int = Field(
        default=0,
        sa_column=Column(sa.BigInteger(), nullable=False, server_default="0"),
    )
    last_heartbeat_at: float = Field(default=0.0, sa_column=Column(sa.Float(), nullable=False, server_default="0"))
    revision: int = 1
    created_at: float = Field(index=True)
    updated_at: float = Field(index=True)


class WorkflowTransitionOutboxDB(SQLModel, table=True):
    """Hub-owned recoverable transition header and authoritative proof."""

    __tablename__ = "workflow_transition_outbox"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_id",
            "command_id",
            name="uq_workflow_transition_command",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "receipt_id",
            name="uq_workflow_transition_receipt",
        ),
        sa.Index(
            "ix_workflow_transition_due",
            "state",
            "available_at",
            "claim_expires_at",
        ),
        sa.Index(
            "ix_workflow_transition_workflow_state",
            "tenant_id",
            "workflow_id",
            "state",
        ),
        sa.Index(
            "ix_workflow_transition_run_created",
            "tenant_id",
            "run_id",
            "created_at",
        ),
        sa.CheckConstraint(
            "expected_revision >= 0 AND attempt_count >= 0 AND claim_generation >= 0 AND revision >= 1",
            name="ck_workflow_transition_non_negative",
        ),
    )

    id: str = Field(sa_column=Column(sa.String(256), primary_key=True))
    tenant_id: str = Field(sa_column=Column(sa.String(256), nullable=False))
    workflow_id: str = Field(sa_column=Column(sa.String(256), nullable=False))
    run_id: str = Field(sa_column=Column(sa.String(256), nullable=False))
    runtime_id: str = Field(sa_column=Column(sa.String(64), nullable=False))
    kind: str = Field(sa_column=Column(sa.String(32), nullable=False))
    request_payload: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
    command_id: Optional[str] = Field(default=None, sa_column=Column(sa.String(256), nullable=True))
    receipt_id: Optional[str] = Field(default=None, sa_column=Column(sa.String(256), nullable=True))
    request_fingerprint: str = Field(sa_column=Column(sa.String(64), nullable=False))
    admitted_command_digest: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    effect_fingerprint: str = Field(sa_column=Column(sa.String(64), nullable=False))
    outcome_fingerprint: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    expected_revision: int
    expected_checkpoint_ref: str = Field(sa_column=Column(sa.String(512), nullable=False))
    result_status: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False, server_default=sa.text("'{}'")),
    )
    result_checkpoint_ref: str = Field(
        default="",
        sa_column=Column(sa.String(512), nullable=False, server_default=""),
    )
    state: str = Field(sa_column=Column(sa.String(32), nullable=False))
    available_at: float
    claim_owner: str = Field(
        default="",
        sa_column=Column(sa.String(256), nullable=False, server_default=""),
    )
    claim_generation: int = Field(
        default=0,
        sa_column=Column(sa.BigInteger(), nullable=False, server_default="0"),
    )
    claim_expires_at: float = Field(default=0.0, sa_column=Column(sa.Float(), nullable=False, server_default="0"))
    last_heartbeat_at: float = Field(default=0.0, sa_column=Column(sa.Float(), nullable=False, server_default="0"))
    attempt_count: int = Field(default=0, sa_column=Column(sa.Integer(), nullable=False, server_default="0"))
    last_error: str = Field(
        default="",
        sa_column=Column(sa.String(160), nullable=False, server_default=""),
    )
    revision: int = Field(default=1, sa_column=Column(sa.Integer(), nullable=False, server_default="1"))
    created_at: float
    updated_at: float
    completed_at: float = Field(default=0.0, sa_column=Column(sa.Float(), nullable=False, server_default="0"))


class WorkflowTransitionEffectDB(SQLModel, table=True):
    """One ordered immutable transition effect and its exact result proof."""

    __tablename__ = "workflow_transition_effects"
    __table_args__ = (
        sa.UniqueConstraint(
            "transition_id",
            "ordinal",
            name="uq_workflow_transition_effect_ordinal",
        ),
        sa.UniqueConstraint(
            "transition_id",
            "idempotency_key",
            name="uq_workflow_transition_effect_key",
        ),
        sa.Index(
            "ix_workflow_transition_effect_state",
            "transition_id",
            "state",
            "ordinal",
        ),
        sa.CheckConstraint(
            "ordinal >= 1 AND applied_generation >= 0 AND revision >= 1",
            name="ck_workflow_transition_effect_non_negative",
        ),
    )

    id: str = Field(sa_column=Column(sa.String(256), primary_key=True))
    transition_id: str = Field(
        sa_column=Column(
            sa.String(256),
            sa.ForeignKey(
                "workflow_transition_outbox.id",
                name="fk_workflow_transition_effect_transition",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    ordinal: int
    kind: str = Field(sa_column=Column(sa.String(32), nullable=False))
    idempotency_key: str = Field(sa_column=Column(sa.String(512), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(sa.JSON, nullable=False))
    payload_digest: str = Field(sa_column=Column(sa.String(64), nullable=False))
    state: str = Field(sa_column=Column(sa.String(32), nullable=False))
    applied_generation: int = Field(
        default=0,
        sa_column=Column(sa.BigInteger(), nullable=False, server_default="0"),
    )
    result_payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(sa.JSON, nullable=False, server_default=sa.text("'{}'")),
    )
    result_digest: str = Field(
        default="",
        sa_column=Column(sa.String(64), nullable=False, server_default=""),
    )
    revision: int = Field(default=1, sa_column=Column(sa.Integer(), nullable=False, server_default="1"))
    created_at: float
    updated_at: float
