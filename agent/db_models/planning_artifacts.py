from __future__ import annotations

import time
import uuid
from typing import Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class PlanningArtifactRevisionDB(SQLModel, table=True):
    """Hub-owned immutable payload revision for category and track planning."""

    __tablename__ = "planning_artifact_revisions"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_planning_artifact_revisions_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_planning_artifact_revisions_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_artifact_revisions_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "parent_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_artifact_revisions_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "project_id",
                "organization_id",
                "supersedes_revision_id",
            ],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_artifact_revisions_supersedes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name="fk_planning_artifact_revisions_approval",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "artifact_id",
            "revision",
            name="uq_planning_artifact_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "id",
            name="uq_planning_artifact_revision_scope_id",
        ),
        sa.CheckConstraint(
            "artifact_type IN ('planning_category_todo', 'planning_track')",
            name="ck_planning_artifact_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'valid', 'failed', 'promoted', 'adopted', 'rejected', 'superseded', 'stale')",
            name="ck_planning_artifact_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_planning_artifact_revision"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    artifact_id: str = Field(index=True, max_length=191)
    revision: int = Field(default=1, ge=1)
    artifact_type: str = Field(index=True, max_length=64)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    status: str = Field(default="draft", index=True, max_length=32)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    content_digest: str = Field(index=True, max_length=64)
    schema_ref: str = Field(max_length=260)
    schema_hash: str = Field(max_length=64)
    prompt_hash: str = Field(default="", max_length=64)
    policy_hash: str = Field(max_length=64)
    source_catalog_id: Optional[str] = Field(default=None, index=True, max_length=191)
    source_catalog_hash: Optional[str] = Field(default=None, max_length=64)
    allowed_source_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    allowed_run_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    source_category_item_ids: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    execution_provenance: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    validation_result: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    parent_revision_id: Optional[str] = Field(default=None, index=True, max_length=191)
    supersedes_revision_id: Optional[str] = Field(default=None, index=True, max_length=191)
    approval_request_id: Optional[str] = Field(default=None, index=True, max_length=191)
    created_by: str = Field(default="hub", max_length=191)
    created_by_principal_id: Optional[str] = Field(default=None, index=True, max_length=191)
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)
    promoted_at: Optional[float] = None
    adopted_at: Optional[float] = None


class PlanningLineageDB(SQLModel, table=True):
    """Lossless Category item -> Track task lineage."""

    __tablename__ = "planning_lineage"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_planning_lineage_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_planning_lineage_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_lineage_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "category_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_lineage_category_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "track_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_lineage_track_revision",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "track_revision_id",
            "plan_task_id",
            "source_category_item_id",
            name="uq_planning_lineage_track_task",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    category_revision_id: str = Field(index=True, max_length=191)
    track_revision_id: str = Field(index=True, max_length=191)
    source_category_item_id: str = Field(index=True, max_length=191)
    plan_task_id: str = Field(max_length=191)
    created_at: float = Field(default_factory=time.time)


class PlanningTaskMappingDB(SQLModel, table=True):
    """Stable mapping created only by the Hub materialization transition."""

    __tablename__ = "planning_task_mappings"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_planning_task_mappings_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_planning_task_mappings_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_task_mappings_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "execution_goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_task_mappings_execution_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "category_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_task_mappings_category_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "track_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_task_mappings_track_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_planning_task_mappings_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "team_id"],
            [
                "organization_team_links.tenant_id",
                "organization_team_links.project_id",
                "organization_team_links.organization_id",
                "organization_team_links.team_id",
            ],
            name="fk_planning_task_mappings_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "role_slot_id"],
            [
                "organization_role_slots.tenant_id",
                "organization_role_slots.project_id",
                "organization_role_slots.organization_id",
                "organization_role_slots.id",
            ],
            name="fk_planning_task_mappings_role_slot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["materialization_receipt_id"],
            ["planning_operation_receipts.id"],
            name="fk_planning_task_mappings_receipt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "track_revision_id",
            "plan_task_id",
            name="uq_planning_task_mapping_plan_task",
        ),
        # One runtime Task may intentionally be referenced by more than one
        # immutable Track revision during a replan.  The per-Track plan-task
        # key remains unique and is the authoritative idempotency boundary.
        sa.UniqueConstraint(
            "track_revision_id",
            "internal_task_id",
            name="uq_planning_task_mapping_track_internal_task",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    execution_goal_id: str = Field(index=True, max_length=191)
    category_revision_id: str = Field(index=True, max_length=191)
    track_revision_id: str = Field(index=True, max_length=191)
    source_category_item_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    plan_task_id: str = Field(max_length=191)
    # Active and archived Tasks intentionally live in separate tables.  The
    # immutable ID is therefore validated by the Hub service across both
    # stores instead of using a FK that would prevent archive transitions.
    internal_task_id: str = Field(index=True, max_length=191)
    unit_id: Optional[str] = Field(default=None, index=True, max_length=191)
    team_id: Optional[str] = Field(default=None, index=True, max_length=191)
    role_slot_id: Optional[str] = Field(default=None, index=True, max_length=191)
    materialization_receipt_id: str = Field(index=True, max_length=191)
    created_at: float = Field(default_factory=time.time)


class PlanningOperationReceiptDB(SQLModel, table=True):
    """Idempotent receipt for Hub-only promotion/adoption/materialization."""

    __tablename__ = "planning_operation_receipts"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_planning_operation_receipts_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_planning_operation_receipts_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_operation_receipts_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "artifact_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_operation_receipts_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name="fk_planning_operation_receipts_approval",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "approval_intent_key",
            "operation",
            name="uq_planning_operation_intent",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_planning_operation_idempotency",
        ),
        sa.CheckConstraint("status = 'committed'", name="ck_planning_operation_receipt_status"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    artifact_revision_id: str = Field(index=True, max_length=191)
    operation: str = Field(index=True, max_length=64)
    approval_intent_key: str = Field(index=True, max_length=64)
    approval_request_id: str = Field(index=True, max_length=191)
    idempotency_key: str = Field(index=True, max_length=191)
    artifact_digest: str = Field(max_length=64)
    policy_hash: str = Field(max_length=64)
    status: str = Field(default="committed", index=True, max_length=32)
    details: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: float = Field(default_factory=time.time)


class WorkerTaskProposalDB(SQLModel, table=True):
    """Untrusted Worker proposal staged in the Hub control plane."""

    __tablename__ = "worker_task_proposals"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_worker_task_proposals_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_worker_task_proposals_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "source_goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_worker_task_proposals_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "unit_id"],
            [
                "organization_units.tenant_id",
                "organization_units.project_id",
                "organization_units.organization_id",
                "organization_units.id",
            ],
            name="fk_worker_task_proposals_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "team_id"],
            [
                "organization_team_links.tenant_id",
                "organization_team_links.project_id",
                "organization_team_links.organization_id",
                "organization_team_links.team_id",
            ],
            name="fk_worker_task_proposals_team",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "role_slot_id"],
            [
                "organization_role_slots.tenant_id",
                "organization_role_slots.project_id",
                "organization_role_slots.organization_id",
                "organization_role_slots.id",
            ],
            name="fk_worker_task_proposals_role_slot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dispatch_lease_id"],
            ["worker_jobs.id"],
            name="fk_worker_task_proposals_dispatch_lease",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name="fk_worker_task_proposals_approval",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "project_id",
                "organization_id",
                "amendment_track_revision_id",
            ],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_worker_task_proposals_amendment_revision",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_task_id",
            "idempotency_key",
            name="uq_worker_task_proposal_idempotency",
        ),
        sa.CheckConstraint(
            "state IN ('submitted', 'rejected', 'needs_approval', "
            "'accepted_as_plan_amendment', 'materialized', 'superseded')",
            name="ck_worker_task_proposal_state",
        ),
        sa.CheckConstraint(
            "proposal_revision >= 1 AND amendment_depth >= 0",
            name="ck_worker_task_proposal_values",
        ),
    )

    proposal_id: str = Field(primary_key=True, max_length=191)
    proposal_revision: int = Field(default=1, ge=1)
    idempotency_key: str = Field(index=True, max_length=191)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    source_goal_id: str = Field(index=True, max_length=191)
    # See PlanningTaskMappingDB.internal_task_id for the dual-table Task
    # identity boundary.
    source_task_id: str = Field(index=True, max_length=191)
    unit_id: str = Field(index=True, max_length=191)
    team_id: str = Field(index=True, max_length=191)
    role_slot_id: str = Field(index=True, max_length=191)
    # Worker assignment/subtask identity is bound to the persisted WorkerJob
    # selected by dispatch_lease_id and revalidated by the capability gate.
    assignment_id: str = Field(index=True, max_length=191)
    dispatch_lease_id: str = Field(index=True, max_length=191)
    proposing_role_template_ref: str = Field(max_length=260)
    proposing_worker_id: str = Field(index=True, max_length=191)
    role_template_version: str = Field(max_length=64)
    payload_digest: str = Field(max_length=71)
    envelope_digest: str = Field(index=True, max_length=71)
    policy_hash: str = Field(max_length=71)
    envelope: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    source_category_item_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    state: str = Field(default="submitted", index=True, max_length=48)
    reason_code: Optional[str] = Field(default=None, index=True, max_length=160)
    decision: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    approval_request_id: Optional[str] = Field(default=None, index=True, max_length=191)
    amendment_track_revision_id: Optional[str] = Field(default=None, index=True, max_length=191)
    materialized_task_id: Optional[str] = Field(default=None, index=True, max_length=191)
    amendment_depth: int = Field(default=0, ge=0)
    budget_estimate: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: float = Field(default_factory=time.time, index=True)
    decided_at: Optional[float] = None
    decided_by: Optional[str] = Field(default=None, max_length=191)


class PlanningAmendmentInputDB(SQLModel, table=True):
    """Hub-staged legacy/manual input awaiting normal research and planning."""

    __tablename__ = "planning_amendment_inputs"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_planning_amendment_inputs_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_planning_amendment_inputs_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_amendment_inputs_goal",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "source_task_id",
            "input_kind",
            "idempotency_key",
            name="uq_planning_amendment_input_idempotency",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_planning_amendment_input_revision"),
        sa.CheckConstraint(
            "state = 'pending_research'",
            name="ck_planning_amendment_input_state",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    revision: int = Field(default=1, ge=1)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    # Kept as a stable logical reference across Task archive/restore moves.
    source_task_id: str = Field(index=True, max_length=191)
    input_kind: str = Field(index=True, max_length=64)
    idempotency_key: str = Field(index=True, max_length=191)
    content_digest: str = Field(index=True, max_length=64)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    state: str = Field(default="pending_research", index=True, max_length=48)
    created_by: str = Field(max_length=191)
    created_at: float = Field(default_factory=time.time, index=True)


class PlanningTaskDispatchDB(SQLModel, table=True):
    """Transactional dispatch intent and lease for execute-next."""

    __tablename__ = "planning_task_dispatches"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_planning_task_dispatches_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_planning_task_dispatches_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_planning_task_dispatches_goal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "track_revision_id"],
            [
                "planning_artifact_revisions.tenant_id",
                "planning_artifact_revisions.project_id",
                "planning_artifact_revisions.organization_id",
                "planning_artifact_revisions.id",
            ],
            name="fk_planning_task_dispatches_track_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_mapping_id"],
            ["planning_task_mappings.id"],
            name="fk_planning_task_dispatches_mapping",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "task_mapping_id",
            "attempt",
            name="uq_planning_task_dispatch_attempt",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_planning_task_dispatch_idempotency",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_planning_task_dispatch_attempt"),
        sa.CheckConstraint(
            "status IN ('pending_dispatch', 'retry_pending', 'dispatching', 'dispatch_failed', 'dispatched')",
            name="ck_planning_task_dispatch_status",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    tenant_id: str = Field(index=True, max_length=191)
    project_id: str = Field(index=True, max_length=191)
    organization_id: str = Field(index=True, max_length=191)
    goal_id: str = Field(index=True, max_length=191)
    track_revision_id: str = Field(index=True, max_length=191)
    task_mapping_id: str = Field(index=True, max_length=191)
    # Dispatch resolution revalidates this logical ID against the active Task
    # row; no single-table FK can represent both active and archived storage.
    internal_task_id: str = Field(index=True, max_length=191)
    dispatch_intent_id: str = Field(index=True, unique=True, max_length=191)
    idempotency_key: str = Field(index=True, max_length=191)
    attempt: int = Field(default=1, ge=1)
    lease_id: str = Field(index=True, unique=True, max_length=191)
    requested_worker_id: Optional[str] = Field(default=None, max_length=512)
    status: str = Field(default="pending_dispatch", index=True, max_length=48)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time, index=True)
    next_attempt_at: float = Field(default_factory=time.time, index=True)
    processing_owner: Optional[str] = Field(default=None, index=True, max_length=191)
    processing_started_at: Optional[float] = Field(default=None, index=True)
    processing_lease_expires_at: Optional[float] = Field(default=None, index=True)
    last_error_code: Optional[str] = Field(default=None, max_length=191)
    worker_job_id: Optional[str] = Field(default=None, index=True, max_length=191)
    assignment_id: Optional[str] = Field(default=None, index=True, max_length=191)
    accepted_worker_id: Optional[str] = Field(default=None, max_length=512)
    transport_receipt: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    accepted_at: Optional[float] = None


__all__ = [
    "PlanningArtifactRevisionDB",
    "PlanningAmendmentInputDB",
    "PlanningLineageDB",
    "PlanningOperationReceiptDB",
    "PlanningTaskMappingDB",
    "PlanningTaskDispatchDB",
    "WorkerTaskProposalDB",
]
