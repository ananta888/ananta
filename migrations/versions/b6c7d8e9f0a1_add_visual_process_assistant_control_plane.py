"""Add persistent Visual Process Assistant control-plane tables.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-07-18 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index(table: str, columns: list[str], *, name: str | None = None) -> None:
    op.create_index(name or f"ix_{table}_{'_'.join(columns)}", table, columns, unique=False)


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "visual_process_assistant_contexts" not in existing:
        op.create_table(
            "visual_process_assistant_contexts",
            sa.Column("context_id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("graph_id", sa.String(), nullable=False),
            sa.Column("definition_revision", sa.Integer(), nullable=False),
            sa.Column("definition_hash", sa.String(), nullable=False),
            sa.Column("editor_mode", sa.String(), nullable=False),
            sa.Column("locale", sa.String(), nullable=False),
            sa.Column("context_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("context_id"),
        )
        for column in ("tenant_id", "owner_subject", "graph_id", "definition_revision", "definition_hash"):
            _index("visual_process_assistant_contexts", [column])

    if "visual_process_assistant_conversations" not in existing:
        op.create_table(
            "visual_process_assistant_conversations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("graph_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("active_context_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["active_context_id"], ["visual_process_assistant_contexts.context_id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("tenant_id", "owner_subject", "graph_id", "status", "active_context_id"):
            _index("visual_process_assistant_conversations", [column])

    if "visual_process_assistant_requests" not in existing:
        op.create_table(
            "visual_process_assistant_requests",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("context_id", sa.String(), nullable=False),
            sa.Column("prompt_context_id", sa.String(), nullable=True),
            sa.Column("prompt_version", sa.String(), nullable=False),
            sa.Column("client_request_id", sa.String(), nullable=False),
            sa.Column("idempotency_key_hash", sa.String(), nullable=False),
            sa.Column("request_fingerprint", sa.String(), nullable=False),
            sa.Column("question_text", sa.String(), nullable=False),
            sa.Column("question_hash", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("retrieval_task_id", sa.String(), nullable=True),
            sa.Column("inference_task_id", sa.String(), nullable=True),
            sa.Column("accepted_evidence_json", sa.JSON(), nullable=False),
            sa.Column("prompt_snapshot_json", sa.JSON(), nullable=True),
            sa.Column("response_json", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("retrieval_deadline_at", sa.Float(), nullable=True),
            sa.Column("inference_deadline_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("cancelled_at", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["conversation_id"], ["visual_process_assistant_conversations.id"]),
            sa.ForeignKeyConstraint(["context_id"], ["visual_process_assistant_contexts.context_id"]),
            sa.ForeignKeyConstraint(["prompt_context_id"], ["visual_process_assistant_contexts.context_id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "owner_subject", "conversation_id", "client_request_id", name="uq_vpa_request_client_scope"
            ),
            sa.UniqueConstraint(
                "tenant_id", "owner_subject", "idempotency_key_hash", name="uq_vpa_request_idempotency_scope"
            ),
        )
        for column in (
            "tenant_id",
            "owner_subject",
            "conversation_id",
            "context_id",
            "prompt_context_id",
            "client_request_id",
            "idempotency_key_hash",
            "request_fingerprint",
            "status",
            "retrieval_task_id",
            "inference_task_id",
        ):
            _index("visual_process_assistant_requests", [column])

    if "visual_process_assistant_rate_limits" not in existing:
        op.create_table(
            "visual_process_assistant_rate_limits",
            sa.Column("bucket_key", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("window_started_at", sa.Float(), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("bucket_key"),
        )
        for column in ("tenant_id", "owner_subject", "window_started_at"):
            _index("visual_process_assistant_rate_limits", [column])

    if "visual_process_patch_audits" not in existing:
        op.create_table(
            "visual_process_patch_audits",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("request_id", sa.String(), nullable=False),
            sa.Column("graph_id", sa.String(), nullable=False),
            sa.Column("context_id", sa.String(), nullable=False),
            sa.Column("prompt_version", sa.String(), nullable=False),
            sa.Column("patch_hash", sa.String(), nullable=False),
            sa.Column("decision", sa.String(), nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("decided_at", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["request_id"], ["visual_process_assistant_requests.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", "patch_hash", name="uq_vpa_patch_request_hash"),
        )
        for column in ("tenant_id", "owner_subject", "request_id", "graph_id", "context_id", "patch_hash", "decision"):
            _index("visual_process_patch_audits", [column])


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "visual_process_patch_audits",
        "visual_process_assistant_rate_limits",
        "visual_process_assistant_requests",
        "visual_process_assistant_conversations",
        "visual_process_assistant_contexts",
    ):
        if table in existing:
            op.drop_table(table)
