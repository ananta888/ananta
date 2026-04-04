"""add missing runtime tables

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-04 15:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _index_names(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {item["name"] for item in inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name in _index_names(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(index_name, columns, unique=False)


def _create_unique_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name in _index_names(table_name):
        return
    with op.batch_alter_table(table_name, schema=None) as batch_op:
        batch_op.create_index(index_name, columns, unique=True)


def upgrade() -> None:
    if not _table_exists("artifacts"):
        op.create_table(
            "artifacts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("latest_version_id", sa.String(), nullable=True),
            sa.Column("latest_sha256", sa.String(), nullable=True),
            sa.Column("latest_media_type", sa.String(), nullable=True),
            sa.Column("latest_filename", sa.String(), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(), nullable=False, server_default="stored"),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("artifact_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _table_exists("artifact_versions"):
        op.create_table(
            "artifact_versions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("artifact_id", sa.String(), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("storage_path", sa.String(), nullable=False),
            sa.Column("original_filename", sa.String(), nullable=False),
            sa.Column("media_type", sa.String(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(), nullable=False),
            sa.Column("version_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("artifact_versions", "ix_artifact_versions_artifact_id", ["artifact_id"])

    if not _table_exists("extracted_documents"):
        op.create_table(
            "extracted_documents",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("artifact_id", sa.String(), nullable=False),
            sa.Column("artifact_version_id", sa.String(), nullable=False),
            sa.Column("extraction_status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("extraction_mode", sa.String(), nullable=False, server_default="raw-only"),
            sa.Column("text_content", sa.Text(), nullable=True),
            sa.Column("document_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("extracted_documents", "ix_extracted_documents_artifact_id", ["artifact_id"])
    _create_index_if_missing("extracted_documents", "ix_extracted_documents_artifact_version_id", ["artifact_version_id"])

    if not _table_exists("knowledge_collections"):
        op.create_table(
            "knowledge_collections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("collection_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_unique_index_if_missing("knowledge_collections", "ix_knowledge_collections_name", ["name"])

    if not _table_exists("knowledge_links"):
        op.create_table(
            "knowledge_links",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("collection_id", sa.String(), nullable=False),
            sa.Column("artifact_id", sa.String(), nullable=False),
            sa.Column("extracted_document_id", sa.String(), nullable=True),
            sa.Column("link_type", sa.String(), nullable=False, server_default="artifact"),
            sa.Column("link_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("knowledge_links", "ix_knowledge_links_collection_id", ["collection_id"])
    _create_index_if_missing("knowledge_links", "ix_knowledge_links_artifact_id", ["artifact_id"])
    _create_index_if_missing("knowledge_links", "ix_knowledge_links_extracted_document_id", ["extracted_document_id"])

    if not _table_exists("retrieval_runs"):
        op.create_table(
            "retrieval_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("query", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("goal_id", sa.String(), nullable=True),
            sa.Column("strategy", sa.JSON(), nullable=True),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("policy_version", sa.String(), nullable=False, server_default="v1"),
            sa.Column("run_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("retrieval_runs", "ix_retrieval_runs_task_id", ["task_id"])
    _create_index_if_missing("retrieval_runs", "ix_retrieval_runs_goal_id", ["goal_id"])

    if not _table_exists("context_bundles"):
        op.create_table(
            "context_bundles",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("retrieval_run_id", sa.String(), nullable=True),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("bundle_type", sa.String(), nullable=False, server_default="worker_execution_context"),
            sa.Column("context_text", sa.Text(), nullable=True),
            sa.Column("chunks", sa.JSON(), nullable=True),
            sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("bundle_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("context_bundles", "ix_context_bundles_retrieval_run_id", ["retrieval_run_id"])
    _create_index_if_missing("context_bundles", "ix_context_bundles_task_id", ["task_id"])

    if not _table_exists("worker_jobs"):
        op.create_table(
            "worker_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("parent_task_id", sa.String(), nullable=True),
            sa.Column("subtask_id", sa.String(), nullable=True),
            sa.Column("worker_url", sa.String(), nullable=False),
            sa.Column("context_bundle_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="created"),
            sa.Column("allowed_tools", sa.JSON(), nullable=True),
            sa.Column("expected_output_schema", sa.JSON(), nullable=True),
            sa.Column("job_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("worker_jobs", "ix_worker_jobs_parent_task_id", ["parent_task_id"])
    _create_index_if_missing("worker_jobs", "ix_worker_jobs_subtask_id", ["subtask_id"])
    _create_index_if_missing("worker_jobs", "ix_worker_jobs_worker_url", ["worker_url"])
    _create_index_if_missing("worker_jobs", "ix_worker_jobs_context_bundle_id", ["context_bundle_id"])

    if not _table_exists("worker_results"):
        op.create_table(
            "worker_results",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("worker_job_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("worker_url", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="received"),
            sa.Column("output", sa.Text(), nullable=True),
            sa.Column("result_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("worker_results", "ix_worker_results_worker_job_id", ["worker_job_id"])
    _create_index_if_missing("worker_results", "ix_worker_results_task_id", ["task_id"])

    if not _table_exists("memory_entries"):
        op.create_table(
            "memory_entries",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("goal_id", sa.String(), nullable=True),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("worker_job_id", sa.String(), nullable=True),
            sa.Column("entry_type", sa.String(), nullable=False, server_default="worker_result"),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("artifact_refs", sa.JSON(), nullable=True),
            sa.Column("retrieval_tags", sa.JSON(), nullable=True),
            sa.Column("memory_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("memory_entries", "ix_memory_entries_task_id", ["task_id"])
    _create_index_if_missing("memory_entries", "ix_memory_entries_goal_id", ["goal_id"])
    _create_index_if_missing("memory_entries", "ix_memory_entries_trace_id", ["trace_id"])
    _create_index_if_missing("memory_entries", "ix_memory_entries_worker_job_id", ["worker_job_id"])


def downgrade() -> None:
    # Deliberately keep downgrade conservative; these tables may contain runtime data.
    pass
