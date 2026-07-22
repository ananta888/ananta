"""add TURN observer, observation and pool directory state

Revision ID: 7f16a7b8c9d0
Revises: 6e05f6a7b8c9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f16a7b8c9d0"
down_revision: str | Sequence[str] | None = "6e05f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_observer_identities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pool_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("region", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active_credential_id", sa.String(), nullable=False),
        sa.Column("previous_credential_id", sa.String(), nullable=True),
        sa.Column("rotation_overlap_until", sa.Float(), nullable=True),
        sa.Column("recovery_evidence_required", sa.Boolean(), nullable=False),
        sa.Column("enrolled_at", sa.Float(), nullable=False),
        sa.Column("rotated_at", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("pool_id", "instance_id", name="uq_turn_observer_pool_instance"),
        sa.CheckConstraint("version > 0", name="ck_turn_observer_identity_version_positive"),
    )
    op.create_index("ix_turn_observer_identity_status_version", "turn_observer_identities", ["status", "version"])
    op.create_table(
        "turn_observer_credentials",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("identity_id", sa.String(), sa.ForeignKey("turn_observer_identities.id"), nullable=False),
        sa.Column("public_key_b64", sa.String(), nullable=False),
        sa.Column("public_key_fingerprint", sa.String(), nullable=False, unique=True),
        sa.Column("certificate_fingerprint", sa.String(), nullable=False, unique=True),
        sa.Column("ca_fingerprint", sa.String(), nullable=False),
        sa.Column("certificate_san", sa.String(), nullable=False),
        sa.Column("certificate_ekus", sa.JSON(), nullable=False),
        sa.Column("proof_nonce_digest", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("valid_from", sa.Float(), nullable=False),
        sa.Column("valid_until", sa.Float(), nullable=False),
        sa.Column("overlap_until", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_turn_observer_credential_identity_status", "turn_observer_credentials", ["identity_id", "status"])
    op.create_table(
        "turn_observer_identity_mutations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("identity_id", sa.String(), nullable=False),
        sa.Column("pool_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("audited_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("actor", "idempotency_key_digest", name="uq_turn_observer_mutation_actor_key"),
    )
    op.create_index("ix_turn_observer_mutation_scope_version", "turn_observer_identity_mutations", ["pool_id", "instance_id", "result_version"])
    op.create_table(
        "turn_observer_enrollment_rate_limits",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("source_digest", sa.String(), nullable=False),
        sa.Column("window_started_at", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("actor", "source_digest", "window_started_at", name="uq_turn_observer_rate_bucket"),
    )
    op.create_table(
        "turn_observation_cursors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pool_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("observer_identity_id", sa.String(), nullable=False),
        sa.Column("observer_identity_version", sa.Integer(), nullable=False),
        sa.Column("current_boot_id", sa.String(), nullable=False),
        sa.Column("retired_boot_ids", sa.JSON(), nullable=False),
        sa.Column("highest_sequence", sa.Integer(), nullable=False),
        sa.Column("last_payload_digest", sa.String(), nullable=False),
        sa.Column("last_observation_id", sa.String(), nullable=False),
        sa.Column("last_measured_at", sa.Float(), nullable=False),
        sa.Column("last_counters_json", sa.JSON(), nullable=False),
        sa.Column("normalized_observation_json", sa.JSON(), nullable=False),
        sa.Column("health_status", sa.String(), nullable=False),
        sa.Column("capacity_status", sa.String(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fresh_until", sa.Float(), nullable=False),
        sa.Column("retain_until", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("pool_id", "instance_id", name="uq_turn_observation_cursor_scope"),
        sa.CheckConstraint("highest_sequence >= 0", name="ck_turn_observation_sequence_non_negative"),
        sa.CheckConstraint("fencing_token > 0", name="ck_turn_observation_fence_positive"),
        sa.CheckConstraint("version > 0", name="ck_turn_observation_version_positive"),
    )
    op.create_index("ix_turn_observation_cursor_freshness", "turn_observation_cursors", ["fresh_until", "retain_until"])
    op.create_table(
        "turn_observation_replays",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pool_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("observation_id_digest", sa.String(), nullable=False, unique=True),
        sa.Column("payload_digest", sa.String(), nullable=False),
        sa.Column("boot_id_digest", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_turn_observation_replay_scope_expiry", "turn_observation_replays", ["pool_id", "instance_id", "expires_at"])
    op.create_table(
        "turn_pool_nodes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pool_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("observer_identity_id", sa.String(), nullable=False),
        sa.Column("region", sa.String(), nullable=False),
        sa.Column("endpoint_urls", sa.JSON(), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("credential_binding_modes", sa.JSON(), nullable=False),
        sa.Column("relay_port_min", sa.Integer(), nullable=False),
        sa.Column("relay_port_max", sa.Integer(), nullable=False),
        sa.Column("allocation_limit", sa.Integer(), nullable=False),
        sa.Column("bps_limit", sa.Integer(), nullable=False),
        sa.Column("cost_profile", sa.String(), nullable=False),
        sa.Column("cost_units", sa.Float(), nullable=False),
        sa.Column("certificate_fingerprint", sa.String(), nullable=False),
        sa.Column("config_digest", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("health_status", sa.String(), nullable=False),
        sa.Column("relay_status", sa.String(), nullable=False),
        sa.Column("capacity_status", sa.String(), nullable=False),
        sa.Column("draining", sa.Boolean(), nullable=False),
        sa.Column("observation_fencing_token", sa.Integer(), nullable=False),
        sa.Column("observation_version", sa.Integer(), nullable=False),
        sa.Column("last_observed_at", sa.Float(), nullable=True),
        sa.Column("fresh_until", sa.Float(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("pool_id", "instance_id", name="uq_turn_pool_node_scope"),
        sa.CheckConstraint("version > 0", name="ck_turn_pool_node_version_positive"),
    )
    op.create_index("ix_turn_pool_node_selection", "turn_pool_nodes", ["region", "status", "health_status", "capacity_status"])
    op.create_table(
        "turn_pool_node_mutations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pool_id", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("audited_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("actor", "idempotency_key_digest", name="uq_turn_pool_mutation_actor_key"),
    )


def downgrade() -> None:
    for table in (
        "turn_pool_node_mutations",
        "turn_pool_nodes",
        "turn_observation_replays",
        "turn_observation_cursors",
        "turn_observer_enrollment_rate_limits",
        "turn_observer_identity_mutations",
        "turn_observer_credentials",
        "turn_observer_identities",
    ):
        op.drop_table(table)
