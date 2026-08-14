"""Add the Hub-owned workflow transition outbox and effect ledger.

Revision ID: d8f0a2c4e6b8
Revises: c7e9a1b3d5f7
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d8f0a2c4e6b8"
down_revision: str | Sequence[str] | None = "c7e9a1b3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BINDINGS = "workflow_control_bindings"
_RECEIPTS = "workflow_control_command_receipts"
_OUTBOX = "workflow_transition_outbox"
_EFFECTS = "workflow_transition_effects"

_BINDING_INDEX = "ix_workflow_control_bindings_active_transition_id"
_RECEIPT_INDEX = "ix_workflow_control_command_receipts_transition_id"


def _binding_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column(
            "active_transition_id",
            sa.String(256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "last_transition_id",
            sa.String(256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "last_transition_command_id",
            sa.String(256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "last_transition_request_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "last_transition_effect_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "last_transition_outcome_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )


def _receipt_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column(
            "request_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "transition_id",
            sa.String(256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "effect_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "outcome_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "dispatch_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_heartbeat_at",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def _outbox_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("tenant_id", sa.String(256), nullable=False),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("runtime_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("command_id", sa.String(256), nullable=True),
        sa.Column("receipt_id", sa.String(256), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "admitted_command_digest",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("effect_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "outcome_fingerprint",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("expected_checkpoint_ref", sa.String(512), nullable=False),
        sa.Column(
            "result_status",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "result_checkpoint_ref",
            sa.String(512),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("available_at", sa.Float(), nullable=False),
        sa.Column(
            "claim_owner",
            sa.String(256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "claim_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "claim_expires_at",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_heartbeat_at",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_error",
            sa.String(160),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column(
            "completed_at",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def _effect_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("transition_id", sa.String(256), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "applied_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "result_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "result_digest",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def _add_missing_columns(
    table_name: str,
    column_factory: Callable[[], tuple[sa.Column[object], ...]],
) -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns(table_name)}
    missing = [column for column in column_factory() if column.name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column in missing:
            batch_op.add_column(column)


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: Sequence[str],
) -> None:
    indexes = {value["name"] for value in inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, list(columns), unique=False)


def _drop_index_if_present(table_name: str, index_name: str) -> None:
    indexes = {value["name"] for value in inspect(op.get_bind()).get_indexes(table_name)}
    if index_name in indexes:
        op.drop_index(index_name, table_name=table_name)


def _drop_columns_if_present(
    table_name: str,
    column_factory: Callable[[], tuple[sa.Column[object], ...]],
) -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}
    names = [column.name for column in column_factory() if column.name in existing]
    if not names:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for name in reversed(names):
            batch_op.drop_column(name)


def _require_c7_tables() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    missing = sorted({_BINDINGS, _RECEIPTS} - tables)
    if missing:
        raise RuntimeError("workflow_transition_outbox_prerequisite_missing:" + ",".join(missing))


def _create_outbox() -> None:
    op.create_table(
        _OUTBOX,
        *_outbox_columns(),
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
        sa.CheckConstraint(
            "expected_revision >= 0 AND attempt_count >= 0 AND claim_generation >= 0 AND revision >= 1",
            name="ck_workflow_transition_non_negative",
        ),
    )


def _create_effects() -> None:
    op.create_table(
        _EFFECTS,
        *_effect_columns(),
        sa.ForeignKeyConstraint(
            ["transition_id"],
            [f"{_OUTBOX}.id"],
            name="fk_workflow_transition_effect_transition",
            ondelete="CASCADE",
        ),
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
        sa.CheckConstraint(
            "ordinal >= 1 AND applied_generation >= 0 AND revision >= 1",
            name="ck_workflow_transition_effect_non_negative",
        ),
    )


def upgrade() -> None:
    _require_c7_tables()
    bind = op.get_bind()

    _add_missing_columns(_BINDINGS, _binding_columns)
    _create_index_if_missing(_BINDINGS, _BINDING_INDEX, ["active_transition_id"])
    _add_missing_columns(_RECEIPTS, _receipt_columns)
    _create_index_if_missing(_RECEIPTS, _RECEIPT_INDEX, ["transition_id"])

    if _OUTBOX not in set(inspect(bind).get_table_names()):
        _create_outbox()
    _create_index_if_missing(
        _OUTBOX,
        "ix_workflow_transition_due",
        ["state", "available_at", "claim_expires_at"],
    )
    _create_index_if_missing(
        _OUTBOX,
        "ix_workflow_transition_workflow_state",
        ["tenant_id", "workflow_id", "state"],
    )
    _create_index_if_missing(
        _OUTBOX,
        "ix_workflow_transition_run_created",
        ["tenant_id", "run_id", "created_at"],
    )

    if _EFFECTS not in set(inspect(bind).get_table_names()):
        _create_effects()
    _create_index_if_missing(
        _EFFECTS,
        "ix_workflow_transition_effect_state",
        ["transition_id", "state", "ordinal"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if _EFFECTS in tables:
        op.drop_table(_EFFECTS)
    tables = set(inspect(bind).get_table_names())
    if _OUTBOX in tables:
        op.drop_table(_OUTBOX)

    tables = set(inspect(bind).get_table_names())
    if _RECEIPTS in tables:
        _drop_index_if_present(_RECEIPTS, _RECEIPT_INDEX)
        _drop_columns_if_present(_RECEIPTS, _receipt_columns)
    tables = set(inspect(bind).get_table_names())
    if _BINDINGS in tables:
        _drop_index_if_present(_BINDINGS, _BINDING_INDEX)
        _drop_columns_if_present(_BINDINGS, _binding_columns)
