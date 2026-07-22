"""add durable SFU Hub control repositories

Revision ID: 9138c9d0e1f2
Revises: 8027b8c9d0e1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "9138c9d0e1f2"
down_revision: str | Sequence[str] | None = "8027b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "sfu_operations_snapshots" not in existing:
        op.create_table(
            "sfu_operations_snapshots",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("snapshot_version", sa.String(), nullable=False),
            sa.Column("snapshot_digest", sa.String(), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("retain_until", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "snapshot_version",
                name="uq_sfu_operations_snapshot_version",
            ),
            sa.CheckConstraint(
                "record_count >= 0",
                name="ck_sfu_operations_snapshot_record_count",
            ),
        )
        op.create_index(
            "ix_sfu_operations_snapshot_current",
            "sfu_operations_snapshots",
            ["created_at", "retain_until"],
        )
    if "sfu_operations_snapshot_records" not in existing:
        op.create_table(
            "sfu_operations_snapshot_records",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "snapshot_id",
                sa.String(),
                sa.ForeignKey(
                    "sfu_operations_snapshots.id", ondelete="CASCADE"
                ),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("observed_at_seconds", sa.Float(), nullable=False),
            sa.Column("tenant_ref", sa.String(), nullable=False),
            sa.Column("region", sa.String(), nullable=False),
            sa.Column("room_ref", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("receiver_ref", sa.String(), nullable=False),
            sa.Column("cohort_size", sa.Integer(), nullable=False),
            sa.Column("group_status", sa.String(), nullable=False),
            sa.Column("route_status", sa.String(), nullable=False),
            sa.Column("epoch_class", sa.String(), nullable=False),
            sa.Column("topology", sa.String(), nullable=False),
            sa.Column("health", sa.String(), nullable=False),
            sa.Column("requested_layer", sa.String(), nullable=False),
            sa.Column("allowed_layer", sa.String(), nullable=False),
            sa.Column("effective_layer", sa.String(), nullable=False),
            sa.Column("layer_none_count", sa.Integer(), nullable=False),
            sa.Column("layer_low_count", sa.Integer(), nullable=False),
            sa.Column("layer_medium_count", sa.Integer(), nullable=False),
            sa.Column("layer_high_count", sa.Integer(), nullable=False),
            sa.Column("queue_depth", sa.Integer(), nullable=False),
            sa.Column("drop_reason", sa.String(), nullable=False),
            sa.Column(
                "ingress_bytes_per_second", sa.Integer(), nullable=False
            ),
            sa.Column(
                "egress_bytes_per_second", sa.Integer(), nullable=False
            ),
            sa.Column(
                "turn_bytes_per_second", sa.Integer(), nullable=False
            ),
            sa.Column("rekey_status", sa.String(), nullable=False),
            sa.Column("failover_status", sa.String(), nullable=False),
            sa.Column("capacity_profile", sa.String(), nullable=False),
            sa.Column("gate_state", sa.String(), nullable=False),
            sa.UniqueConstraint(
                "snapshot_id",
                "ordinal",
                name="uq_sfu_operations_snapshot_ordinal",
            ),
            sa.CheckConstraint(
                "ordinal >= 0 AND cohort_size >= 0 AND queue_depth >= 0",
                name="ck_sfu_operations_record_counts",
            ),
            sa.CheckConstraint(
                "ingress_bytes_per_second >= 0 "
                "AND egress_bytes_per_second >= 0 "
                "AND turn_bytes_per_second >= 0",
                name="ck_sfu_operations_record_rates",
            ),
        )
        op.create_index(
            "ix_sfu_operations_record_scope",
            "sfu_operations_snapshot_records",
            ["snapshot_id", "tenant_ref", "region", "ordinal"],
        )
    if "sfu_command_idempotency_ledger" not in existing:
        op.create_table(
            "sfu_command_idempotency_ledger",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("scope_digest", sa.String(), nullable=False),
            sa.Column("key_digest", sa.String(), nullable=False),
            sa.Column("request_digest", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("result_accepted", sa.Boolean(), nullable=True),
            sa.Column(
                "result_effective_version", sa.Integer(), nullable=True
            ),
            sa.Column("result_state", sa.String(), nullable=True),
            sa.Column("result_reason_code", sa.String(), nullable=True),
            sa.Column("result_command_ref", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "scope_digest",
                "key_digest",
                name="uq_sfu_command_ledger_scope_key",
            ),
            sa.CheckConstraint(
                "status IN ('pending','completed')",
                name="ck_sfu_command_ledger_status",
            ),
            sa.CheckConstraint(
                "version > 0", name="ck_sfu_command_ledger_version"
            ),
        )
        op.create_index(
            "ix_sfu_command_ledger_retention",
            "sfu_command_idempotency_ledger",
            ["expires_at", "scope_digest"],
        )
    if "sfu_fanout_reconciliation_controls" not in existing:
        op.create_table(
            "sfu_fanout_reconciliation_controls",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("owner_digest", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column(
                "lease_expires_at_ms", sa.BigInteger(), nullable=False
            ),
            sa.Column("checkpoint_phase", sa.String(), nullable=True),
            sa.Column("checkpoint_token", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("retain_until_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "room_id",
                name="uq_sfu_fanout_reconciliation_scope",
            ),
            sa.CheckConstraint(
                "fencing_token >= 0 AND version > 0",
                name="ck_sfu_fanout_reconciliation_fencing",
            ),
            sa.CheckConstraint(
                "checkpoint_phase IS NULL "
                "OR checkpoint_phase IN ('revoke','ensure')",
                name="ck_sfu_fanout_reconciliation_phase",
            ),
        )
        op.create_index(
            "ix_sfu_fanout_reconciliation_lease",
            "sfu_fanout_reconciliation_controls",
            ["tenant_id", "lease_expires_at_ms"],
        )
    if "sfu_fanout_reconciliation_outcomes" not in existing:
        op.create_table(
            "sfu_fanout_reconciliation_outcomes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("candidate_digest", sa.String(), nullable=False),
            sa.Column("outcome_digest", sa.String(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("retryable", sa.Boolean(), nullable=False),
            sa.Column("mutation_outcome", sa.String(), nullable=True),
            sa.Column("mutation_reason_code", sa.String(), nullable=True),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("retain_until_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "room_id",
                "candidate_digest",
                "fencing_token",
                name="uq_sfu_fanout_reconciliation_outcome",
            ),
            sa.CheckConstraint(
                "fencing_token > 0",
                name="ck_sfu_fanout_reconciliation_outcome_fence",
            ),
        )
        op.create_index(
            "ix_sfu_fanout_reconciliation_outcome_retention",
            "sfu_fanout_reconciliation_outcomes",
            ["tenant_id", "room_id", "retain_until_ms"],
        )
    if "sfu_scope_epoch_authorities" not in existing:
        op.create_table(
            "sfu_scope_epoch_authorities",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("actor_digest", sa.String(), nullable=False),
            sa.Column("admission_epoch", sa.Integer(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("route_epoch", sa.Integer(), nullable=False),
            sa.Column("topology_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("retain_until_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "room_id",
                "actor_digest",
                name="uq_sfu_scope_epoch_actor",
            ),
            sa.CheckConstraint(
                "admission_epoch > 0 AND membership_epoch > 0 "
                "AND route_epoch >= 0 AND topology_epoch >= 0 "
                "AND key_epoch >= 0",
                name="ck_sfu_scope_epoch_values",
            ),
            sa.CheckConstraint(
                "fencing_token > 0 AND version > 0",
                name="ck_sfu_scope_epoch_fencing",
            ),
            sa.CheckConstraint(
                "status IN ('active','revoked')",
                name="ck_sfu_scope_epoch_status",
            ),
        )
        op.create_index(
            "ix_sfu_scope_epoch_active",
            "sfu_scope_epoch_authorities",
            ["tenant_id", "room_id", "status", "expires_at_ms"],
        )
    if "sfu_scope_epoch_grants" not in existing:
        op.create_table(
            "sfu_scope_epoch_grants",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("room_id", sa.String(), nullable=False),
            sa.Column("actor_digest", sa.String(), nullable=False),
            sa.Column("projection_kind", sa.String(), nullable=False),
            sa.Column("subject_digest", sa.String(), nullable=False),
            sa.Column("scope_version", sa.Integer(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("retain_until_ms", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "room_id",
                "actor_digest",
                "projection_kind",
                "subject_digest",
                name="uq_sfu_scope_epoch_grant",
            ),
            sa.CheckConstraint(
                "projection_kind IN ('publisher','receiver')",
                name="ck_sfu_scope_epoch_grant_kind",
            ),
            sa.CheckConstraint(
                "scope_version > 0 AND membership_epoch > 0 "
                "AND fencing_token > 0",
                name="ck_sfu_scope_epoch_grant_fencing",
            ),
            sa.CheckConstraint(
                "status IN ('active','revoked')",
                name="ck_sfu_scope_epoch_grant_status",
            ),
        )
        op.create_index(
            "ix_sfu_scope_epoch_grant_active",
            "sfu_scope_epoch_grants",
            [
                "tenant_id",
                "room_id",
                "actor_digest",
                "status",
                "expires_at_ms",
            ],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        "sfu_scope_epoch_grants",
        "sfu_scope_epoch_authorities",
        "sfu_fanout_reconciliation_outcomes",
        "sfu_fanout_reconciliation_controls",
        "sfu_command_idempotency_ledger",
        "sfu_operations_snapshot_records",
        "sfu_operations_snapshots",
    ):
        if table in existing:
            op.drop_table(table)
