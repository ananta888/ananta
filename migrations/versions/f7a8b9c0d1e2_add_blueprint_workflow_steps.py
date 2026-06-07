"""add blueprint_workflow_steps table (WFG-005)

Revision ID: f7a8b9c0d1e2
Revises: e8f9a0b1c2d3
Create Date: 2026-06-07 22:00:00.000000

WFG-005: adds the persistent table for the optional workflow block
introduced in WFG-001. Mirrors the validated workflow steps from
seed_blueprint_catalog.v1 (one row per step, scoped to a blueprint
via FK) so the planner and queue layers can query steps without
re-parsing the catalog JSON on every request.

DAG edges (depends_on, produces, consumes) live in JSON columns
rather than relational tables — see the BlueprintWorkflowStepDB
docstring for the trade-off.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy import inspect


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = ("e8f9a0b1c2d3", "f2e3d4c5b6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _existing_indexes(table_name: str) -> set[str]:
    return {idx["name"] for idx in inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    if "blueprint_workflow_steps" in _existing_tables():
        return

    op.create_table(
        "blueprint_workflow_steps",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blueprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("step_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("role_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("task_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="coding"),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("produces", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("consumes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("depends_on", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("gate", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("checks", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("failure_policy", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("required_capabilities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Float(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["blueprint_id"], ["team_blueprints.id"], name="fk_blueprint_workflow_steps_blueprint_id"),
        sa.UniqueConstraint("blueprint_id", "step_id", name="uq_blueprint_workflow_steps_blueprint_step_id"),
        sa.UniqueConstraint("blueprint_id", "sort_order", name="uq_blueprint_workflow_steps_blueprint_sort_order"),
    )

    existing = _existing_indexes("blueprint_workflow_steps")
    if "ix_blueprint_workflow_steps_blueprint_id" not in existing:
        op.create_index("ix_blueprint_workflow_steps_blueprint_id", "blueprint_workflow_steps", ["blueprint_id"])
    if "ix_blueprint_workflow_steps_step_id" not in existing:
        op.create_index("ix_blueprint_workflow_steps_step_id", "blueprint_workflow_steps", ["step_id"])
    if "ix_blueprint_workflow_steps_role_name" not in existing:
        op.create_index("ix_blueprint_workflow_steps_role_name", "blueprint_workflow_steps", ["role_name"])


def downgrade() -> None:
    if "blueprint_workflow_steps" not in _existing_tables():
        return
    op.drop_table("blueprint_workflow_steps")
