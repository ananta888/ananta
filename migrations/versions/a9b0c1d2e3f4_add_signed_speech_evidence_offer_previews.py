"""Persist signed content-free speech-evidence offer previews.

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-19 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_PREVIEW_DIGEST = "229f6172d235c0787c183e3af3d7c1eb68680e7f5e219b0a02ef2023e8366baa"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "speech_evidence_offers" not in tables:
        return
    columns = {row["name"] for row in inspect(bind).get_columns("speech_evidence_offers")}
    with op.batch_alter_table("speech_evidence_offers") as batch:
        if "group_previews" not in columns:
            batch.add_column(
                sa.Column("group_previews", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )
        if "group_preview_digest" not in columns:
            batch.add_column(
                sa.Column(
                    "group_preview_digest",
                    sa.String(),
                    nullable=False,
                    server_default=_EMPTY_PREVIEW_DIGEST,
                )
            )
        if "protocol_version" not in columns:
            batch.add_column(
                sa.Column(
                    "protocol_version",
                    sa.String(),
                    nullable=False,
                    server_default="ananta.speech-evidence-sync.v1",
                )
            )
    indexes = {row["name"] for row in inspect(bind).get_indexes("speech_evidence_offers")}
    if "ix_speech_evidence_offers_group_preview_digest" not in indexes:
        op.create_index(
            "ix_speech_evidence_offers_group_preview_digest",
            "speech_evidence_offers",
            ["group_preview_digest"],
        )
    if "ix_speech_evidence_offers_protocol_version" not in indexes:
        op.create_index(
            "ix_speech_evidence_offers_protocol_version",
            "speech_evidence_offers",
            ["protocol_version"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "speech_evidence_offers" not in set(inspect(bind).get_table_names()):
        return
    indexes = {row["name"] for row in inspect(bind).get_indexes("speech_evidence_offers")}
    for name in (
        "ix_speech_evidence_offers_protocol_version",
        "ix_speech_evidence_offers_group_preview_digest",
    ):
        if name in indexes:
            op.drop_index(name, table_name="speech_evidence_offers")
    columns = {row["name"] for row in inspect(bind).get_columns("speech_evidence_offers")}
    with op.batch_alter_table("speech_evidence_offers") as batch:
        for name in ("protocol_version", "group_preview_digest", "group_previews"):
            if name in columns:
                batch.drop_column(name)
