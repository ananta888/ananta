"""Persistent Hub-owned runtime state for enterprise Organizations.

The models in this module deliberately store control-plane state only.  They
reference existing task and artifact identities but never copy task bodies,
artifact contents, prompts, credentials, or Worker-local state.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _json_column(default: Any) -> sa.Column:
    return sa.Column(sa.JSON(), nullable=False, default=default)


_ORGANIZATION_FK = (
    ["tenant_id", "project_id", "organization_id"],
    [
        "organization_instances.tenant_id",
        "organization_instances.project_id",
        "organization_instances.organization_id",
    ],
)

_GOAL_FK_TARGET = ["goals.tenant_id", "goals.project_id", "goals.id"]
_UNIT_FK_TARGET = [
    "organization_units.tenant_id",
    "organization_units.project_id",
    "organization_units.organization_id",
    "organization_units.id",
]
_TEAM_FK_TARGET = [
    "organization_team_links.tenant_id",
    "organization_team_links.project_id",
    "organization_team_links.organization_id",
    "organization_team_links.team_id",
]
_ROLE_SLOT_FK_TARGET = [
    "organization_role_slots.tenant_id",
    "organization_role_slots.project_id",
    "organization_role_slots.organization_id",
    "organization_role_slots.id",
]


class OrganizationBudgetUsageDB(SQLModel, table=True):
    """Current aggregate consumption for one hierarchical budget scope."""

    __tablename__ = "organization_budget_usage"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            *_ORGANIZATION_FK,
            name="fk_organization_budget_usage_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "scope_kind",
            "scope_id",
            name="uq_organization_budget_usage_scope",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('organization', 'unit', 'team', 'workflow', 'task')",
            name="ck_organization_budget_usage_scope_kind",
        ),
        sa.CheckConstraint(
            "tokens_used >= 0 AND cost_used >= 0 AND wall_seconds_used >= 0 "
            "AND parallel_slots_reserved >= 0 AND revision >= 1",
            name="ck_organization_budget_usage_values",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    scope_kind: str = Field(index=True, max_length=32)
    scope_id: str = Field(index=True, max_length=191)
    tokens_used: int = Field(default=0, ge=0)
    cost_used: Decimal = Field(
        default=Decimal("0"),
        sa_column=sa.Column(sa.Numeric(24, 8), nullable=False, default=Decimal("0")),
    )
    wall_seconds_used: int = Field(default=0, ge=0)
    parallel_slots_reserved: int = Field(default=0, ge=0)
    revision: int = Field(default=1, ge=1)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class OrganizationBudgetReservationDB(SQLModel, table=True):
    """Idempotent reservation and final settlement receipt."""

    __tablename__ = "organization_budget_reservations"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            *_ORGANIZATION_FK,
            name="fk_organization_budget_reservations_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "unit_id"],
            _UNIT_FK_TARGET,
            name="fk_organization_budget_reservations_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "team_id"],
            _TEAM_FK_TARGET,
            name="fk_organization_budget_reservations_team",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "reservation_id",
            name="uq_organization_budget_reservation_scope_id",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'cancelled', 'denied')",
            name="ck_organization_budget_reservation_status",
        ),
        sa.CheckConstraint(
            "requested_tokens >= 0 AND requested_cost >= 0 "
            "AND requested_wall_seconds >= 0 AND requested_parallel_slots > 0 "
            "AND revision >= 1",
            name="ck_organization_budget_reservation_values",
        ),
        sa.CheckConstraint(
            "(actual_tokens IS NULL OR actual_tokens >= 0) "
            "AND (actual_cost IS NULL OR actual_cost >= 0) "
            "AND (actual_wall_seconds IS NULL OR actual_wall_seconds >= 0)",
            name="ck_organization_budget_reservation_actual_values",
        ),
        sa.CheckConstraint(
            "status <> 'settled' OR "
            "(actual_tokens IS NOT NULL AND actual_cost IS NOT NULL "
            "AND actual_wall_seconds IS NOT NULL AND settlement_digest IS NOT NULL "
            "AND settled_at IS NOT NULL)",
            name="ck_organization_budget_reservation_settlement",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    reservation_id: str = Field(index=True, max_length=191)
    unit_id: str | None = Field(default=None, index=True, max_length=191)
    team_id: str | None = Field(default=None, index=True, max_length=191)
    workflow_id: str | None = Field(default=None, index=True, max_length=191)
    task_id: str = Field(index=True, max_length=191)
    model_profile: str = Field(max_length=191)
    requested_tokens: int = Field(ge=0)
    requested_cost: Decimal = Field(sa_column=sa.Column(sa.Numeric(24, 8), nullable=False))
    requested_wall_seconds: int = Field(ge=0)
    requested_parallel_slots: int = Field(gt=0)
    actual_tokens: int | None = Field(default=None, ge=0)
    actual_cost: Decimal | None = Field(
        default=None,
        sa_column=sa.Column(sa.Numeric(24, 8), nullable=True),
    )
    actual_wall_seconds: int | None = Field(default=None, ge=0)
    limits_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=_json_column(list))
    request_digest: str = Field(max_length=64)
    settlement_digest: str | None = Field(default=None, max_length=64)
    policy_hash: str = Field(max_length=64)
    reason_code: str = Field(default="organization_budget_reserved", max_length=191)
    exceeded_scopes: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    status: str = Field(default="reserved", index=True, max_length=16)
    revision: int = Field(default=1, ge=1)
    created_at: float = Field(default_factory=time.time)
    settled_at: float | None = None


class OrganizationRuntimeEventDB(SQLModel, table=True):
    """Redacted, immutable, per-Organization ordered event envelope."""

    __tablename__ = "organization_runtime_events"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            *_ORGANIZATION_FK,
            name="fk_organization_runtime_events_organization",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "event_id",
            name="uq_organization_runtime_event_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "sequence",
            name="uq_organization_runtime_event_sequence",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_organization_runtime_event_sequence"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    event_id: str = Field(index=True, max_length=191)
    event_type: str = Field(index=True, max_length=64)
    definition_revision: str = Field(max_length=64)
    snapshot_hash: str = Field(max_length=64)
    correlation_id: str = Field(index=True, max_length=191)
    sequence: int = Field(ge=1)
    occurred_at: str = Field(max_length=40)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    semantic_digest: str = Field(max_length=64)
    created_at: float = Field(default_factory=time.time)


class OrganizationTeamHandoffDB(SQLModel, table=True):
    """CAS-protected handoff state containing references, never artifact data."""

    __tablename__ = "organization_team_handoffs"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            *_ORGANIZATION_FK,
            name="fk_organization_team_handoffs_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            _GOAL_FK_TARGET,
            name="fk_organization_team_handoffs_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "producer_unit_id"],
            _UNIT_FK_TARGET,
            name="fk_organization_team_handoffs_producer_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "producer_team_id"],
            _TEAM_FK_TARGET,
            name="fk_organization_team_handoffs_producer_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "producer_role_slot_id"],
            _ROLE_SLOT_FK_TARGET,
            name="fk_organization_team_handoffs_producer_role_slot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "consumer_unit_id"],
            _UNIT_FK_TARGET,
            name="fk_organization_team_handoffs_consumer_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "consumer_team_id"],
            _TEAM_FK_TARGET,
            name="fk_organization_team_handoffs_consumer_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "consumer_role_slot_id"],
            _ROLE_SLOT_FK_TARGET,
            name="fk_organization_team_handoffs_consumer_role_slot",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "handoff_id",
            name="uq_organization_team_handoff_scope_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending_acceptance', 'accepted', 'rejected', 'needs_changes', 'cancelled')",
            name="ck_organization_team_handoff_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_organization_team_handoff_revision"),
        sa.CheckConstraint("sla_seconds > 0", name="ck_organization_team_handoff_sla"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    handoff_id: str = Field(index=True, max_length=191)
    correlation_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    producer_unit_id: str = Field(index=True, max_length=191)
    producer_team_id: str = Field(index=True, max_length=191)
    producer_role_slot_id: str = Field(index=True, max_length=191)
    producer_task_id: str = Field(index=True, max_length=191)
    consumer_unit_id: str = Field(index=True, max_length=191)
    consumer_team_id: str = Field(index=True, max_length=191)
    consumer_role_slot_id: str = Field(index=True, max_length=191)
    consumer_task_id: str = Field(index=True, max_length=191)
    contract_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    contract_digest: str = Field(max_length=64)
    artifact_digests: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    status: str = Field(default="pending_acceptance", index=True, max_length=32)
    reason_code: str = Field(max_length=191)
    idempotency_key: str = Field(max_length=191)
    decision_idempotency_key: str | None = Field(default=None, max_length=191)
    decided_by_principal_id: str | None = Field(default=None, max_length=191)
    revision: int = Field(default=1, ge=1)
    due_at: str = Field(max_length=40)
    sla_seconds: int = Field(gt=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    resolved_at: float | None = None


class OrganizationWorkflowLoopStateDB(SQLModel, table=True):
    """Persisted bounded feedback/rework loop instance."""

    __tablename__ = "organization_workflow_loop_states"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            *_ORGANIZATION_FK,
            name="fk_organization_workflow_loops_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "unit_id"],
            _UNIT_FK_TARGET,
            name="fk_organization_workflow_loops_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "team_id"],
            _TEAM_FK_TARGET,
            name="fk_organization_workflow_loops_team",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "loop_instance_id",
            name="uq_organization_workflow_loop_scope_id",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'rework_requested', 'completed', 'blocked', 'escalated', 'cancelled')",
            name="ck_organization_workflow_loop_status",
        ),
        sa.CheckConstraint(
            "iteration >= 0 AND accumulated_cost >= 0 AND revision >= 1",
            name="ck_organization_workflow_loop_values",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    loop_instance_id: str = Field(index=True, max_length=191)
    loop_id: str = Field(index=True, max_length=191)
    workflow_id: str | None = Field(default=None, index=True, max_length=191)
    task_id: str | None = Field(default=None, index=True, max_length=191)
    unit_id: str | None = Field(default=None, index=True, max_length=191)
    team_id: str | None = Field(default=None, index=True, max_length=191)
    definition_revision: str = Field(max_length=64)
    snapshot_hash: str = Field(max_length=64)
    policy_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column(dict))
    iteration: int = Field(default=0, ge=0)
    status: str = Field(default="running", index=True, max_length=32)
    started_at: str = Field(max_length=40)
    updated_at: str = Field(max_length=40)
    accumulated_cost: Decimal = Field(
        default=Decimal("0"),
        sa_column=sa.Column(sa.Numeric(24, 8), nullable=False, default=Decimal("0")),
    )
    artifact_versions: list[str] = Field(default_factory=list, sa_column=_json_column(list))
    selected_transition: str | None = Field(default=None, max_length=255)
    reason_code: str = Field(default="loop_started", max_length=191)
    last_idempotency_key: str = Field(max_length=191)
    last_request_digest: str = Field(max_length=64)
    revision: int = Field(default=1, ge=1)
    created_at: float = Field(default_factory=time.time)


__all__ = [name for name in globals() if name.endswith("DB")]
