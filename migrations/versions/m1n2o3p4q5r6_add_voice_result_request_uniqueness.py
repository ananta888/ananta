"""Add scoped Voice result request uniqueness.

Revision ID: m1n2o3p4q5r6
Revises: l1m2n3o4p5q6
Create Date: 2026-07-12 00:00:04.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "m1n2o3p4q5r6"
down_revision: str | Sequence[str] | None = "l1m2n3o4p5q6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_voice_result_artifact_scope_request_kind"


def upgrade() -> None:
    if "voice_result_artifacts" not in set(inspect(op.get_bind()).get_table_names()):
        return
    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT tenant_id, owner_subject, request_hash, artifact_kind "
            "FROM voice_result_artifacts "
            "GROUP BY tenant_id, owner_subject, request_hash, artifact_kind "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "voice_result_artifacts contains duplicate scoped request artifacts; "
            "resolve them before applying the uniqueness migration"
        )
    unique_names = {
        item.get("name") for item in inspect(op.get_bind()).get_unique_constraints("voice_result_artifacts")
    }
    if _CONSTRAINT not in unique_names:
        with op.batch_alter_table("voice_result_artifacts") as batch_op:
            batch_op.create_unique_constraint(
                _CONSTRAINT,
                ["tenant_id", "owner_subject", "request_hash", "artifact_kind"],
            )


def downgrade() -> None:
    if "voice_result_artifacts" not in set(inspect(op.get_bind()).get_table_names()):
        return
    unique_names = {
        item.get("name") for item in inspect(op.get_bind()).get_unique_constraints("voice_result_artifacts")
    }
    if _CONSTRAINT in unique_names:
        with op.batch_alter_table("voice_result_artifacts") as batch_op:
            batch_op.drop_constraint(_CONSTRAINT, type_="unique")
