"""Add atomic SQL speech-adapter authority and training lease revisions.

Revision ID: da0b1c2d3e4f
Revises: d9e0f1a2b3c4
Create Date: 2026-07-19 23:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "da0b1c2d3e4f"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADAPTER_INDEX_COLUMNS = (
    "tenant_id",
    "owner_subject",
    "pair_id",
    "direction",
    "speaker_digest",
    "scope_digest",
    "base_model_id",
    "base_model_digest",
    "backend",
    "backend_digest",
    "dataset_digest",
    "split_digest",
    "evaluation_report_digest",
    "consent_digest",
    "consent_expires_at_ms",
    "artifact_sha256",
    "expires_at_ms",
    "status",
    "approved_by_digest",
    "approved_at_ms",
    "revoked_at_ms",
    "deprecated_at_ms",
    "expired_at_ms",
    "rollback_of_adapter_id",
    "created_at_ms",
)


def upgrade() -> None:
    _add_lease_version("ml_intern_training_capacity_leases")
    _add_lease_version("ml_intern_training_execution_leases")
    if "ml_intern_speech_adapters" not in _tables():
        _create_adapter_table()
    else:
        _upgrade_legacy_adapter_projection()
    if "ml_intern_speech_adapter_legacy_imports" not in _tables():
        op.create_table(
            "ml_intern_speech_adapter_legacy_imports",
            sa.Column("source_digest", sa.String(), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("imported_at_ms", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("source_digest"),
        )
        op.create_index(
            "ix_ml_intern_speech_adapter_legacy_imports_imported_at_ms",
            "ml_intern_speech_adapter_legacy_imports",
            ["imported_at_ms"],
        )


def downgrade() -> None:
    tables = _tables()
    if "ml_intern_speech_adapter_legacy_imports" in tables:
        op.drop_table("ml_intern_speech_adapter_legacy_imports")
    if "ml_intern_speech_adapters" in tables:
        op.drop_table("ml_intern_speech_adapters")
    for table_name in (
        "ml_intern_training_execution_leases",
        "ml_intern_training_capacity_leases",
    ):
        if table_name in _tables() and "version" in _columns(table_name):
            with op.batch_alter_table(table_name) as batch:
                batch.drop_column("version")


def _create_adapter_table() -> None:
    op.create_table(
        "ml_intern_speech_adapters",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("pair_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("speaker_digest", sa.String(), nullable=False),
        sa.Column("scope_digest", sa.String(), nullable=False),
        sa.Column("base_model_id", sa.String(), nullable=False),
        sa.Column("base_model_digest", sa.String(), nullable=False),
        sa.Column("backend", sa.String(), nullable=False),
        sa.Column("backend_digest", sa.String(), nullable=False),
        sa.Column("dataset_digest", sa.String(), nullable=False),
        sa.Column("split_digest", sa.String(), nullable=False),
        sa.Column("evaluation_report_digest", sa.String(), nullable=False),
        sa.Column("evaluation_policy_version", sa.String(), nullable=False),
        sa.Column("evaluation_passed", sa.Boolean(), nullable=False),
        sa.Column("evaluation_approval_eligible", sa.Boolean(), nullable=False),
        sa.Column("consent_digest", sa.String(), nullable=False),
        sa.Column("consent_expires_at_ms", sa.Integer(), nullable=False),
        sa.Column("artifact_ref", sa.String(), nullable=False),
        sa.Column("artifact_sha256", sa.String(), nullable=False),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=False),
        sa.Column("expires_at_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("approved_by_digest", sa.String(), nullable=True),
        sa.Column("approval_reason_code", sa.String(), nullable=True),
        sa.Column("approved_at_ms", sa.Integer(), nullable=True),
        sa.Column("revoked_at_ms", sa.Integer(), nullable=True),
        sa.Column("deprecated_at_ms", sa.Integer(), nullable=True),
        sa.Column("expired_at_ms", sa.Integer(), nullable=True),
        sa.Column("rollback_of_adapter_id", sa.String(), nullable=True),
        sa.Column("lineage", sa.JSON(), nullable=True),
        sa.Column("adapter_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_subject",
            "id",
            name="uq_speech_adapter_scope_id",
        ),
    )
    _create_adapter_indexes()


def _upgrade_legacy_adapter_projection() -> None:
    additions = {
        "evaluation_policy_version": sa.Column(
            "evaluation_policy_version",
            sa.String(),
            nullable=False,
            server_default="legacy-v1",
        ),
        "evaluation_passed": sa.Column(
            "evaluation_passed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        "evaluation_approval_eligible": sa.Column(
            "evaluation_approval_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        "approved_by_digest": sa.Column("approved_by_digest", sa.String(), nullable=True),
        "approval_reason_code": sa.Column("approval_reason_code", sa.String(), nullable=True),
        "approved_at_ms": sa.Column("approved_at_ms", sa.Integer(), nullable=True),
        "revoked_at_ms": sa.Column("revoked_at_ms", sa.Integer(), nullable=True),
        "deprecated_at_ms": sa.Column("deprecated_at_ms", sa.Integer(), nullable=True),
        "rollback_of_adapter_id": sa.Column("rollback_of_adapter_id", sa.String(), nullable=True),
        "lineage": sa.Column("lineage", sa.JSON(), nullable=True),
    }
    existing = _columns("ml_intern_speech_adapters")
    for name, column in additions.items():
        if name not in existing:
            op.add_column("ml_intern_speech_adapters", column)
    _create_adapter_indexes()


def _create_adapter_indexes() -> None:
    table_name = "ml_intern_speech_adapters"
    existing = {item["name"] for item in inspect(op.get_bind()).get_indexes(table_name)}
    for column in _ADAPTER_INDEX_COLUMNS:
        name = f"ix_{table_name}_{column}"
        if name not in existing:
            op.create_index(name, table_name, [column])
    composite = "ix_speech_adapter_pair_direction_status"
    if composite not in existing:
        op.create_index(
            composite,
            table_name,
            ["tenant_id", "owner_subject", "pair_id", "direction", "status"],
        )


def _add_lease_version(table_name: str) -> None:
    if table_name in _tables() and "version" not in _columns(table_name):
        op.add_column(
            table_name,
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table_name)}
