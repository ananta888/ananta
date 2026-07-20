"""Add durable Hub control-plane state for speech-evidence sync.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-19 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_peer_keys" not in existing:
        op.create_table(
            "speech_evidence_peer_keys",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("sender_id", sa.String(), nullable=False),
            sa.Column("audience_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("public_key_b64", sa.String(), nullable=False),
            sa.Column("fingerprint", sa.String(), nullable=False),
            sa.Column("membership_version", sa.Integer(), nullable=False),
            sa.Column("consent_version", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "session_id",
                "pair_id",
                "sender_id",
                "audience_id",
                "epoch",
                "key_id",
                name="uq_speech_evidence_peer_key_scope",
            ),
        )
        for column in (
            "tenant_id",
            "session_id",
            "pair_id",
            "sender_id",
            "audience_id",
            "epoch",
            "key_id",
            "fingerprint",
            "state",
            "expires_at_ms",
        ):
            op.create_index(f"ix_speech_evidence_peer_keys_{column}", "speech_evidence_peer_keys", [column])
        op.create_index(
            "ix_speech_evidence_peer_key_discovery",
            "speech_evidence_peer_keys",
            ["tenant_id", "session_id", "sender_id", "audience_id", "epoch", "state"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_replay_states" not in existing:
        op.create_table(
            "speech_evidence_replay_states",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("sender_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("traffic_class", sa.String(), nullable=False),
            sa.Column("highest_sequence", sa.BigInteger(), nullable=False),
            sa.Column("bitmap_hex", sa.String(), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "session_id",
                "pair_id",
                "sender_id",
                "epoch",
                "traffic_class",
                name="uq_speech_evidence_replay_context",
            ),
        )
        for column in ("session_id", "pair_id", "sender_id", "epoch", "traffic_class", "expires_at_ms"):
            op.create_index(
                f"ix_speech_evidence_replay_states_{column}", "speech_evidence_replay_states", [column]
            )
        op.create_index(
            "ix_speech_evidence_replay_expiry",
            "speech_evidence_replay_states",
            ["expires_at_ms", "updated_at_ms"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_offers" not in existing:
        op.create_table(
            "speech_evidence_offers",
            sa.Column("offer_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("proposal_verification_digest", sa.String(), nullable=False),
            sa.Column("acceptance_verification_digest", sa.String(), nullable=True),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("sender_id", sa.String(), nullable=False),
            sa.Column("recipient_id", sa.String(), nullable=False),
            sa.Column("inventory_root_digest", sa.String(), nullable=False),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("purpose", sa.String(), nullable=False),
            sa.Column("data_classes", sa.JSON(), nullable=False),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("retention_seconds", sa.Integer(), nullable=False),
            sa.Column("trainer_class", sa.String(), nullable=False),
            sa.Column("group_ids", sa.JSON(), nullable=False),
            sa.Column("total_bytes", sa.BigInteger(), nullable=False),
            sa.Column("sender_consent_digest", sa.String(), nullable=False),
            sa.Column("recipient_consent_digest", sa.String(), nullable=False),
            sa.Column("scope_digest", sa.String(), nullable=False),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("transfer_started", sa.Boolean(), nullable=False),
            sa.Column("invalidation_reason", sa.String(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint("offer_id"),
        )
        for column in (
            "tenant_id",
            "proposal_verification_digest",
            "acceptance_verification_digest",
            "session_id",
            "pair_id",
            "epoch",
            "sender_id",
            "recipient_id",
            "scope_digest",
            "expires_at_ms",
            "state",
        ):
            op.create_index(f"ix_speech_evidence_offers_{column}", "speech_evidence_offers", [column])
        op.create_index(
            "ix_speech_evidence_offer_scope",
            "speech_evidence_offers",
            ["tenant_id", "session_id", "pair_id", "state", "expires_at_ms"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_transfers" not in existing:
        op.create_table(
            "speech_evidence_transfers",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("offer_id", sa.String(), nullable=False),
            sa.Column("group_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("pair_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("sender_id", sa.String(), nullable=False),
            sa.Column("recipient_id", sa.String(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("chunk_count", sa.Integer(), nullable=False),
            sa.Column("first_missing_index", sa.Integer(), nullable=False),
            sa.Column("acknowledged_indices", sa.JSON(), nullable=False),
            sa.Column("received_bytes", sa.BigInteger(), nullable=False),
            sa.Column("in_flight_bytes", sa.BigInteger(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(["offer_id"], ["speech_evidence_offers.offer_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("offer_id", "group_id", name="uq_speech_evidence_transfer_group"),
        )
        for column in (
            "tenant_id",
            "offer_id",
            "group_id",
            "session_id",
            "pair_id",
            "epoch",
            "sender_id",
            "recipient_id",
            "key_id",
            "state",
            "expires_at_ms",
        ):
            op.create_index(f"ix_speech_evidence_transfers_{column}", "speech_evidence_transfers", [column])
        op.create_index(
            "ix_speech_evidence_transfer_scope",
            "speech_evidence_transfers",
            ["tenant_id", "session_id", "sender_id", "recipient_id", "state"],
        )

    existing = set(inspect(op.get_bind()).get_table_names())
    if "speech_evidence_transfer_chunks" not in existing:
        op.create_table(
            "speech_evidence_transfer_chunks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("transfer_id", sa.String(), nullable=False),
            sa.Column("message_id", sa.String(), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("plaintext_bytes", sa.Integer(), nullable=False),
            sa.Column("plaintext_digest", sa.String(), nullable=False),
            sa.Column("ciphertext_digest", sa.String(), nullable=False),
            sa.Column("nonce_digest", sa.String(), nullable=False),
            sa.Column("nonce_scope_digest", sa.String(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("epoch", sa.Integer(), nullable=False),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("acknowledged", sa.Boolean(), nullable=False),
            sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(["transfer_id"], ["speech_evidence_transfers.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("transfer_id", "chunk_index", name="uq_speech_evidence_transfer_chunk_index"),
            sa.UniqueConstraint("transfer_id", "message_id", name="uq_speech_evidence_transfer_chunk_message"),
            sa.UniqueConstraint(
                "nonce_scope_digest", name="uq_speech_evidence_transfer_nonce"
            ),
        )
        for column in (
            "transfer_id",
            "message_id",
            "chunk_index",
            "nonce_digest",
            "nonce_scope_digest",
            "key_id",
            "epoch",
            "direction",
            "acknowledged",
        ):
            op.create_index(
                f"ix_speech_evidence_transfer_chunks_{column}", "speech_evidence_transfer_chunks", [column]
            )


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "speech_evidence_transfer_chunks",
        "speech_evidence_transfers",
        "speech_evidence_offers",
        "speech_evidence_replay_states",
        "speech_evidence_peer_keys",
    ):
        if table in existing:
            op.drop_table(table)
