"""Add workflow-control observation fences and durable dispatch intents.

Revision ID: c7e9a1b3d5f7
Revises: a6c8e1f3b5d7
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "c7e9a1b3d5f7"
down_revision: str | Sequence[str] | None = "a6c8e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_control_bindings"
_COLUMNS = (
    (
        "public_status",
        sa.JSON(),
        sa.text("'{}'"),
    ),
    (
        "command_observation_pending",
        sa.Boolean(),
        sa.text("false"),
    ),
    (
        "command_observation_min_revision",
        sa.Integer(),
        sa.text("0"),
    ),
    (
        "command_observation_expected_status",
        sa.String(64),
        sa.text("''"),
    ),
    (
        "dispatch_intent_id",
        sa.String(256),
        sa.text("''"),
    ),
    (
        "command_receipt_id",
        sa.String(256),
        sa.text("''"),
    ),
)
_INDEX = "ix_workflow_control_bindings_command_observation_pending"
_DISPATCH_INDEX = "ix_workflow_control_bindings_dispatch_intent_id"
_RECEIPT_BINDING_INDEX = "ix_workflow_control_bindings_command_receipt_id"
_INTENTS_TABLE = "workflow_control_dispatch_intents"
_RECEIPTS_TABLE = "workflow_control_command_receipts"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    columns = {value["name"] for value in inspector.get_columns(_TABLE)}
    for name, column_type, default in _COLUMNS:
        if name not in columns:
            op.add_column(
                _TABLE,
                sa.Column(
                    name,
                    column_type,
                    nullable=False,
                    server_default=default,
                ),
            )
    indexes = {value["name"] for value in inspect(bind).get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(
            _INDEX,
            _TABLE,
            ["command_observation_pending"],
            unique=False,
        )
    indexes = {value["name"] for value in inspect(bind).get_indexes(_TABLE)}
    if _DISPATCH_INDEX not in indexes:
        op.create_index(
            _DISPATCH_INDEX,
            _TABLE,
            ["dispatch_intent_id"],
            unique=False,
        )
    indexes = {value["name"] for value in inspect(bind).get_indexes(_TABLE)}
    if _RECEIPT_BINDING_INDEX not in indexes:
        op.create_index(
            _RECEIPT_BINDING_INDEX,
            _TABLE,
            ["command_receipt_id"],
            unique=False,
        )
    if _INTENTS_TABLE not in set(inspect(bind).get_table_names()):
        op.create_table(
            _INTENTS_TABLE,
            sa.Column("id", sa.String(256), primary_key=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("tenant_id", sa.String(256), nullable=False),
            sa.Column("workflow_id", sa.String(256), nullable=False),
            sa.Column("run_id", sa.String(256), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column(
                "dispatch_from_state",
                sa.String(32),
                nullable=False,
                server_default="ready",
            ),
            sa.Column(
                "acknowledgement_revision",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "acknowledgement_status",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("available_at", sa.Float(), nullable=False),
            sa.Column(
                "lease_owner",
                sa.String(256),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "lease_expires_at",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "last_error",
                sa.String(256),
                nullable=False,
                server_default="",
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
        op.create_index(
            "ix_workflow_control_dispatch_due",
            _INTENTS_TABLE,
            ["state", "available_at", "lease_expires_at"],
        )
        op.create_index(
            "ix_workflow_control_dispatch_workflow",
            _INTENTS_TABLE,
            ["workflow_id", "state"],
        )
        for column in ("kind", "tenant_id", "workflow_id", "run_id", "state"):
            op.create_index(
                f"ix_{_INTENTS_TABLE}_{column}",
                _INTENTS_TABLE,
                [column],
            )
        for column in ("available_at", "lease_owner", "lease_expires_at", "created_at", "updated_at"):
            op.create_index(
                f"ix_{_INTENTS_TABLE}_{column}",
                _INTENTS_TABLE,
                [column],
            )
    if _RECEIPTS_TABLE not in set(inspect(bind).get_table_names()):
        op.create_table(
            _RECEIPTS_TABLE,
            sa.Column("id", sa.String(256), primary_key=True),
            sa.Column("tenant_id", sa.String(256), nullable=False),
            sa.Column("workflow_id", sa.String(256), nullable=False),
            sa.Column("run_id", sa.String(256), nullable=False),
            sa.Column("actor_id", sa.String(256), nullable=False),
            sa.Column("command_type", sa.String(64), nullable=False),
            sa.Column("request_payload", sa.JSON(), nullable=False),
            sa.Column("expected_revision", sa.Integer(), nullable=False),
            sa.Column("checkpoint_ref", sa.String(512), nullable=False),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column("result_status", sa.JSON(), nullable=False),
            sa.Column(
                "rejection_reason",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "dispatch_owner",
                sa.String(256),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "dispatch_lease_expires_at",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
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
        op.create_index(
            "ix_workflow_control_command_receipts_workflow_state",
            _RECEIPTS_TABLE,
            ["workflow_id", "state"],
        )
        for column in (
            "tenant_id",
            "workflow_id",
            "run_id",
            "actor_id",
            "command_type",
            "state",
            "dispatch_owner",
            "created_at",
            "updated_at",
        ):
            op.create_index(
                f"ix_{_RECEIPTS_TABLE}_{column}",
                _RECEIPTS_TABLE,
                [column],
            )
        op.create_index(
            f"ix_{_RECEIPTS_TABLE}_dispatch_lease_expires_at",
            _RECEIPTS_TABLE,
            ["dispatch_lease_expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _RECEIPTS_TABLE in set(inspector.get_table_names()):
        op.drop_table(_RECEIPTS_TABLE)
    inspector = inspect(bind)
    if _INTENTS_TABLE in set(inspector.get_table_names()):
        op.drop_table(_INTENTS_TABLE)
    if _TABLE not in set(inspector.get_table_names()):
        return
    indexes = {value["name"] for value in inspector.get_indexes(_TABLE)}
    if _RECEIPT_BINDING_INDEX in indexes:
        op.drop_index(_RECEIPT_BINDING_INDEX, table_name=_TABLE)
    if _DISPATCH_INDEX in indexes:
        op.drop_index(_DISPATCH_INDEX, table_name=_TABLE)
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)
    columns = {value["name"] for value in inspect(bind).get_columns(_TABLE)}
    for name, _column_type, _default in reversed(_COLUMNS):
        if name in columns:
            op.drop_column(_TABLE, name)
