from __future__ import annotations

import time
from typing import List, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class TaskDB(SQLModel, table=True):
    __tablename__ = "tasks"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_tasks_project_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_tasks_organization_scope",
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
            name="fk_tasks_unit_scope",
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
            name="fk_tasks_organization_team_scope",
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
            name="fk_tasks_role_slot_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "plan_node_id"],
            [
                "plan_nodes.tenant_id",
                "plan_nodes.project_id",
                "plan_nodes.organization_id",
                "plan_nodes.id",
            ],
            name="fk_tasks_plan_node_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_tasks_goal_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            name="fk_tasks_plan_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "id",
            name="uq_tasks_organization_scope_id",
        ),
    )
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "todo"
    priority: str = "Medium"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    team_id: Optional[str] = Field(default=None, foreign_key="teams.id", index=True)
    organization_id: Optional[str] = Field(default=None, index=True)
    unit_id: Optional[str] = Field(default=None, index=True)
    role_slot_id: Optional[str] = Field(default=None, index=True)
    tenant_id: Optional[str] = Field(default=None, index=True, max_length=191)
    project_id: Optional[str] = Field(default=None, index=True, max_length=191)
    assigned_agent_url: Optional[str] = Field(default=None, foreign_key="agents.url")
    assigned_role_id: Optional[str] = Field(default=None, foreign_key="roles.id")
    history: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
    last_proposal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    last_output: Optional[str] = None
    last_exit_code: Optional[int] = None
    callback_url: Optional[str] = None
    callback_token: Optional[str] = None
    manual_override_until: Optional[float] = None
    goal_id: Optional[str] = Field(default=None, index=True)
    goal_trace_id: Optional[str] = Field(default=None, index=True)
    plan_id: Optional[str] = Field(default=None, index=True)
    plan_node_id: Optional[str] = Field(default=None, index=True)
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    context_bundle_id: Optional[str] = Field(default=None, index=True)
    worker_execution_context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    current_worker_job_id: Optional[str] = Field(default=None, index=True)
    verification_spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    verification_status: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status_reason_code: Optional[str] = None
    status_reason_details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: int = 0
    depends_on: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    kanban_position: int = Field(default=0, index=True)
    kanban_revision: int = Field(default=0, index=True)


class ArchivedTaskDB(SQLModel, table=True):
    __tablename__ = "archived_tasks"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_archived_tasks_project_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id"],
            [
                "organization_instances.tenant_id",
                "organization_instances.project_id",
                "organization_instances.organization_id",
            ],
            name="fk_archived_tasks_organization_scope",
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
            name="fk_archived_tasks_unit_scope",
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
            name="fk_archived_tasks_organization_team_scope",
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
            name="fk_archived_tasks_role_slot_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "organization_id", "plan_node_id"],
            [
                "plan_nodes.tenant_id",
                "plan_nodes.project_id",
                "plan_nodes.organization_id",
                "plan_nodes.id",
            ],
            name="fk_archived_tasks_plan_node_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "goal_id"],
            ["goals.tenant_id", "goals.project_id", "goals.id"],
            name="fk_archived_tasks_goal_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            name="fk_archived_tasks_plan_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "organization_id",
            "id",
            name="uq_archived_tasks_organization_scope_id",
        ),
    )
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "archived"
    priority: str = "Medium"
    created_at: float
    updated_at: float
    archived_at: float = Field(default_factory=time.time)
    team_id: Optional[str] = Field(default=None, foreign_key="teams.id", index=True)
    organization_id: Optional[str] = Field(default=None, index=True)
    unit_id: Optional[str] = Field(default=None, index=True)
    role_slot_id: Optional[str] = Field(default=None, index=True)
    tenant_id: Optional[str] = Field(default=None, index=True, max_length=191)
    project_id: Optional[str] = Field(default=None, index=True, max_length=191)
    assigned_agent_url: Optional[str] = None
    assigned_role_id: Optional[str] = None
    history: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
    last_proposal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    last_output: Optional[str] = None
    last_exit_code: Optional[int] = None
    callback_url: Optional[str] = None
    callback_token: Optional[str] = None
    manual_override_until: Optional[float] = None
    goal_id: Optional[str] = Field(default=None, index=True)
    goal_trace_id: Optional[str] = None
    plan_id: Optional[str] = Field(default=None, index=True)
    plan_node_id: Optional[str] = Field(default=None, index=True)
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    context_bundle_id: Optional[str] = None
    worker_execution_context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    current_worker_job_id: Optional[str] = None
    verification_spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    verification_status: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status_reason_code: Optional[str] = None
    status_reason_details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: int = 0
    depends_on: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    kanban_position: int = Field(default=0)
    kanban_revision: int = Field(default=0)


_TASK_ARCHIVE_FIELDS = tuple(TaskDB.model_fields)
if set(_TASK_ARCHIVE_FIELDS) != set(ArchivedTaskDB.model_fields) - {"archived_at"}:
    raise RuntimeError("task_archive_model_field_parity_violation")


def archive_task_record(task: TaskDB) -> ArchivedTaskDB:
    """Copy the complete active Task contract into its archive record."""

    return ArchivedTaskDB(**{field_name: getattr(task, field_name) for field_name in _TASK_ARCHIVE_FIELDS})


def restore_task_record(task: ArchivedTaskDB) -> TaskDB:
    """Restore only fields that belong to the active Task contract."""

    return TaskDB(**{field_name: getattr(task, field_name) for field_name in _TASK_ARCHIVE_FIELDS})


class ConfigDB(SQLModel, table=True):
    __tablename__ = "config"
    key: str = Field(primary_key=True)
    value_json: str
