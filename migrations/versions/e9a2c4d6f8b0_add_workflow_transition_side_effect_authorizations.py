"""Add append-only workflow transition side-effect authorization receipts.

Revision ID: e9a2c4d6f8b0
Revises: d8f0a2c4e6b8
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "e9a2c4d6f8b0"
down_revision: str | Sequence[str] | None = "d8f0a2c4e6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_transition_side_effect_authorizations"
_OPERATION_INDEX = "ix_workflow_transition_side_effect_auth_operation"
_TENANT_RUN_INDEX = "ix_workflow_transition_side_effect_auth_tenant_run"
_TRANSITION_INDEX = "ix_workflow_transition_side_effect_auth_transition"
_LEDGER = "workflow_side_effect_ledger"

_STRING_LENGTHS = {
    "receipt_id": 256,
    "transition_id": 256,
    "effect_id": 256,
    "operation_id": 256,
    "operation_fence_id": 256,
    "tenant_id": 256,
    "workflow_id": 256,
    "run_id": 256,
    "runtime_id": 64,
    "step_id": 256,
    "operation_intent_digest": 64,
    "authorization_envelope_id": 256,
    "authorization_envelope_digest": 64,
    "ownership_attempt_id": 256,
    "receipt_digest": 64,
}
_BIG_INTEGERS = {
    "ownership_fencing_token",
    "creator_claim_generation",
    "authorized_ledger_revision",
}
_FLOATS = {"planned_at", "authorized_at"}
_UNIQUE_COLUMNS = {
    "uq_workflow_transition_side_effect_auth_effect": ("effect_id",),
    "uq_workflow_transition_side_effect_auth_fence": ("operation_fence_id",),
    "uq_workflow_transition_side_effect_auth_revision": (
        "operation_id",
        "authorized_ledger_revision",
    ),
}
_INDEX_COLUMNS = {
    _OPERATION_INDEX: ("operation_id",),
    _TENANT_RUN_INDEX: ("tenant_id", "run_id"),
    _TRANSITION_INDEX: ("transition_id",),
}


def _create_table() -> None:
    op.create_table(
        _TABLE,
        sa.Column("receipt_id", sa.String(256), primary_key=True),
        sa.Column("transition_id", sa.String(256), nullable=False),
        sa.Column("effect_id", sa.String(256), nullable=False),
        sa.Column("operation_id", sa.String(256), nullable=False),
        sa.Column("operation_fence_id", sa.String(256), nullable=False),
        sa.Column("tenant_id", sa.String(256), nullable=False),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("runtime_id", sa.String(64), nullable=False),
        sa.Column("step_id", sa.String(256), nullable=False),
        sa.Column("operation_intent_digest", sa.String(64), nullable=False),
        sa.Column("authorization_envelope_id", sa.String(256), nullable=False),
        sa.Column("authorization_envelope_digest", sa.String(64), nullable=False),
        sa.Column("ownership_attempt_id", sa.String(256), nullable=False),
        sa.Column("ownership_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("creator_claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("authorized_ledger_revision", sa.BigInteger(), nullable=False),
        sa.Column("planned_at", sa.Float(), nullable=False),
        sa.Column("authorized_at", sa.Float(), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
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
    )


def _create_index_if_missing(name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    existing = {value["name"] for value in inspect(bind).get_indexes(_TABLE)}
    if name not in existing:
        op.create_index(name, _TABLE, columns, unique=False)


def _validate_table(*, require_indexes: bool) -> None:
    inspector = inspect(op.get_bind())
    columns = {value["name"]: value for value in inspector.get_columns(_TABLE)}
    expected_names = set(_STRING_LENGTHS) | _BIG_INTEGERS | _FLOATS | {"receipt"}
    if set(columns) != expected_names:
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    if any(value["nullable"] for value in columns.values()):
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    for name, length in _STRING_LENGTHS.items():
        value_type = columns[name]["type"]
        if not isinstance(value_type, sa.String) or value_type.length != length:
            raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    if any(not isinstance(columns[name]["type"], sa.BigInteger) for name in _BIG_INTEGERS):
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    if any(not isinstance(columns[name]["type"], sa.Float) for name in _FLOATS):
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    if not isinstance(columns["receipt"]["type"], sa.JSON):
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    primary_key = inspector.get_pk_constraint(_TABLE)
    if tuple(primary_key.get("constrained_columns") or ()) != ("receipt_id",):
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    unique_constraints = {
        value["name"]: tuple(value["column_names"]) for value in inspector.get_unique_constraints(_TABLE)
    }
    if unique_constraints != _UNIQUE_COLUMNS:
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    checks = {value["name"] for value in inspector.get_check_constraints(_TABLE)}
    if checks != {"ck_workflow_transition_side_effect_auth_positive"}:
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    if inspector.get_foreign_keys(_TABLE):
        raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")
    if require_indexes:
        indexes = {
            value["name"]: tuple(value["column_names"])
            for value in inspector.get_indexes(_TABLE)
            if not value.get("unique")
        }
        if indexes != _INDEX_COLUMNS:
            raise RuntimeError("workflow_transition_side_effect_authorization_schema_conflict")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if _LEDGER not in tables:
        raise RuntimeError("workflow_transition_side_effect_authorization_prerequisite_missing")
    if _TABLE not in tables:
        _create_table()
    _validate_table(require_indexes=False)
    _create_index_if_missing(_OPERATION_INDEX, ["operation_id"])
    _create_index_if_missing(_TENANT_RUN_INDEX, ["tenant_id", "run_id"])
    _create_index_if_missing(_TRANSITION_INDEX, ["transition_id"])
    _validate_table(require_indexes=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE in set(inspect(bind).get_table_names()):
        op.drop_table(_TABLE)
