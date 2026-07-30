"""Harden source-control outbox, bulk resume, purge and policy bindings.

Revision ID: 1e6f8a0c2d4b
Revises: 0d5f7b9c1e3a
Create Date: 2026-07-30
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "1e6f8a0c2d4b"
down_revision: str | Sequence[str] | None = "0d5f7b9c1e3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_access_grants",
        sa.Column("policy_snapshot_digest", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_source_access_grants_policy_snapshot_digest",
        "source_access_grants",
        ["policy_snapshot_digest"],
    )
    op.create_table(
        "source_control_operations",
        sa.Column("idempotency_key", sa.String(96), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("claim_token", sa.String(96), nullable=True),
        sa.Column("lease_expires_at_epoch", sa.Float(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
    )
    for column in (
        "request_digest",
        "operation",
        "state",
        "claim_token",
        "lease_expires_at_epoch",
    ):
        op.create_index(
            f"ix_source_control_operations_{column}",
            "source_control_operations",
            [column],
        )

    op.create_table(
        "source_control_job_event_outbox",
        sa.Column(
            "sequence",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("event_id", sa.String(70), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=True),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("occurred_at_epoch", sa.Float(), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "event_id",
            name="uq_source_control_job_event_outbox_event",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "job_id",
            "event_id",
            name="uq_source_control_job_event_outbox_scope_job_event",
        ),
    )
    for column in (
        "event_id",
        "tenant_id",
        "project_id",
        "resource_id",
        "job_id",
        "event_type",
        "status",
        "trace_id",
    ):
        op.create_index(
            f"ix_source_control_job_event_outbox_{column}",
            "source_control_job_event_outbox",
            [column],
        )

    op.create_table(
        "source_control_bulk_target_checkpoints",
        sa.Column("checkpoint_id", sa.String(80), primary_key=True),
        sa.Column(
            "idempotency_key",
            sa.String(96),
            sa.ForeignKey("source_control_operations.idempotency_key"),
            nullable=False,
        ),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("target_ordinal", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("target_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            "target_ordinal",
            name="uq_source_control_bulk_checkpoint_ordinal",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            "target_digest",
            name="uq_source_control_bulk_checkpoint_target",
        ),
    )
    for column in (
        "idempotency_key",
        "plan_digest",
        "resource_id",
        "target_digest",
        "state",
    ):
        op.create_index(
            f"ix_source_control_bulk_target_checkpoints_{column}",
            "source_control_bulk_target_checkpoints",
            [column],
        )

    op.create_table(
        "source_control_purge_approvals",
        sa.Column("approval_id", sa.String(96), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("claim_id", sa.String(96), nullable=True),
        sa.Column("claim_expires_at_epoch", sa.Float(), nullable=True),
        sa.Column("issued_at_epoch", sa.Float(), nullable=False),
        sa.Column("expires_at_epoch", sa.Float(), nullable=False),
        sa.Column("consumed_at_epoch", sa.Float(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
    )
    for column in (
        "tenant_id",
        "project_id",
        "action",
        "object_type",
        "object_id",
        "request_digest",
        "approved_by",
        "state",
        "claim_id",
        "claim_expires_at_epoch",
        "expires_at_epoch",
    ):
        op.create_index(
            f"ix_source_control_purge_approvals_{column}",
            "source_control_purge_approvals",
            [column],
        )

    op.create_table(
        "source_control_index_references",
        sa.Column("binding_id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("knowledge_index_id", sa.String(128), nullable=False),
        sa.Column("reference_kind", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(255), nullable=False),
        sa.Column("reference_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("expires_at_epoch", sa.Float(), nullable=True),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("released_at_epoch", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "knowledge_index_id",
            "reference_kind",
            "reference_id",
            name="uq_source_control_index_reference",
        ),
    )
    for column in (
        "tenant_id",
        "project_id",
        "knowledge_index_id",
        "reference_kind",
        "reference_id",
        "state",
        "expires_at_epoch",
    ):
        op.create_index(
            f"ix_source_control_index_references_{column}",
            "source_control_index_references",
            [column],
        )

    bind = op.get_bind()
    policies = bind.execute(
        sa.text(
            "SELECT tenant_id, project_id, policy_id, version, "
            "policy_digest FROM source_control_context_policy_versions"
        )
    ).mappings()
    snapshots: dict[tuple[str, str, str], str] = {}
    for row in policies:
        payload = {
            "tenant_id": row["tenant_id"],
            "project_id": row["project_id"],
            "policy_id": row["policy_id"],
            "version": int(row["version"]),
            "policy_digest": row["policy_digest"],
        }
        snapshot_id = "cpv_" + hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        snapshots[
            (row["tenant_id"], row["project_id"], snapshot_id)
        ] = row["policy_digest"]
    for (tenant_id, project_id, snapshot_id), digest in snapshots.items():
        bind.execute(
            sa.text(
                "UPDATE source_access_grants "
                "SET policy_snapshot_digest = :digest "
                "WHERE tenant_id = :tenant_id "
                "AND project_id = :project_id "
                "AND policy_version = :snapshot_id"
            ),
            {
                "digest": digest,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "snapshot_id": snapshot_id,
            },
        )

    legacy_events = bind.execute(
        sa.text(
            "SELECT event_id, tenant_id, project_id, connection_id, "
            "action, to_knowledge_index_id, occurred_at_epoch "
            "FROM active_knowledge_index_events "
            "ORDER BY occurred_at_epoch, event_id"
        )
    ).mappings()
    event_types = {
        "activate": "index_activated",
        "rollback": "index_rolled_back",
        "reconcile": "index_reconciled",
    }
    for sequence, row in enumerate(legacy_events, start=1):
        bind.execute(
            sa.text(
                "INSERT INTO source_control_job_event_outbox "
                "(sequence, event_id, tenant_id, project_id, resource_id, "
                "job_id, event_type, status, reason_code, trace_id, "
                "occurred_at_epoch, created_at_epoch) VALUES "
                "(:sequence, :event_id, :tenant_id, :project_id, "
                ":resource_id, :job_id, :event_type, 'completed', NULL, "
                ":trace_id, :occurred_at_epoch, :created_at_epoch)"
            ),
            {
                "sequence": sequence,
                "event_id": row["event_id"],
                "tenant_id": row["tenant_id"],
                "project_id": row["project_id"],
                "resource_id": row["connection_id"],
                "job_id": row["to_knowledge_index_id"],
                "event_type": event_types.get(
                    row["action"], "index_reconciled"
                ),
                "trace_id": row["event_id"],
                "occurred_at_epoch": row["occurred_at_epoch"],
                "created_at_epoch": row["occurred_at_epoch"],
            },
        )


def downgrade() -> None:
    op.drop_table("source_control_index_references")
    op.drop_table("source_control_purge_approvals")
    op.drop_table("source_control_bulk_target_checkpoints")
    op.drop_table("source_control_job_event_outbox")
    op.drop_table("source_control_operations")
    op.drop_index(
        "ix_source_access_grants_policy_snapshot_digest",
        table_name="source_access_grants",
    )
    op.drop_column(
        "source_access_grants", "policy_snapshot_digest"
    )
