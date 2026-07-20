"""Add bounded Hub quality-wave and explicit training-budget state.

Revision ID: fc1d2e3f4a5b
Revises: eb1c2d3e4f5a
Create Date: 2026-07-20 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "fc1d2e3f4a5b"
down_revision: str | Sequence[str] | None = "eb1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("speech_reconciliation_jobs")}
    if "current_compute_factor" not in columns:
        with op.batch_alter_table("speech_reconciliation_jobs") as batch:
            batch.add_column(sa.Column("current_compute_factor", sa.Integer(), nullable=True))
        op.execute(
            "UPDATE speech_reconciliation_jobs "
            "SET current_compute_factor = CASE "
            "WHEN max_compute_factor < 10 THEN max_compute_factor ELSE 10 END "
            "WHERE current_compute_factor IS NULL"
        )
        with op.batch_alter_table("speech_reconciliation_jobs") as batch:
            batch.alter_column("current_compute_factor", nullable=False)
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("speech_reconciliation_jobs")}
    with op.batch_alter_table("speech_reconciliation_jobs") as batch:
        if "quality_history" not in columns:
            batch.add_column(sa.Column("quality_history", sa.JSON(), nullable=False, server_default="[]"))
        if "training_budget" not in columns:
            batch.add_column(sa.Column("training_budget", sa.JSON(), nullable=True))
    checkpoint_constraints = {
        constraint["name"]
        for constraint in inspect(op.get_bind()).get_unique_constraints("speech_reconciliation_checkpoints")
        if constraint.get("name")
    }
    old_checkpoint_constraint = "uq_speech_reconciliation_checkpoint_sequence"
    new_checkpoint_constraint = "uq_speech_reconciliation_checkpoint_attempt_sequence"
    if old_checkpoint_constraint in checkpoint_constraints or new_checkpoint_constraint not in checkpoint_constraints:
        with op.batch_alter_table("speech_reconciliation_checkpoints") as batch:
            if old_checkpoint_constraint in checkpoint_constraints:
                batch.drop_constraint(old_checkpoint_constraint, type_="unique")
            if new_checkpoint_constraint not in checkpoint_constraints:
                batch.create_unique_constraint(
                    new_checkpoint_constraint,
                    ["job_id", "attempt_id", "checkpoint_sequence"],
                )


def downgrade() -> None:
    checkpoint_constraints = {
        constraint["name"]
        for constraint in inspect(op.get_bind()).get_unique_constraints("speech_reconciliation_checkpoints")
        if constraint.get("name")
    }
    old_checkpoint_constraint = "uq_speech_reconciliation_checkpoint_sequence"
    new_checkpoint_constraint = "uq_speech_reconciliation_checkpoint_attempt_sequence"
    if new_checkpoint_constraint in checkpoint_constraints or old_checkpoint_constraint not in checkpoint_constraints:
        with op.batch_alter_table("speech_reconciliation_checkpoints") as batch:
            if new_checkpoint_constraint in checkpoint_constraints:
                batch.drop_constraint(new_checkpoint_constraint, type_="unique")
            if old_checkpoint_constraint not in checkpoint_constraints:
                batch.create_unique_constraint(
                    old_checkpoint_constraint,
                    ["job_id", "checkpoint_sequence"],
                )
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("speech_reconciliation_jobs")}
    with op.batch_alter_table("speech_reconciliation_jobs") as batch:
        for column in ("training_budget", "quality_history", "current_compute_factor"):
            if column in columns:
                batch.drop_column(column)
