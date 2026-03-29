"""add knowledge indices

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _index_names(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {item["name"] for item in inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    if not _table_exists("knowledge_indices"):
        op.create_table(
            "knowledge_indices",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("artifact_id", sa.String(), nullable=True),
            sa.Column("collection_id", sa.String(), nullable=True),
            sa.Column("latest_run_id", sa.String(), nullable=True),
            sa.Column("source_scope", sa.String(), nullable=False),
            sa.Column("profile_name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("output_dir", sa.String(), nullable=True),
            sa.Column("manifest_path", sa.String(), nullable=True),
            sa.Column("index_metadata", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    with op.batch_alter_table("knowledge_indices", schema=None) as batch_op:
        if "ix_knowledge_indices_artifact_id" not in _index_names("knowledge_indices"):
            batch_op.create_index("ix_knowledge_indices_artifact_id", ["artifact_id"], unique=False)
        if "ix_knowledge_indices_collection_id" not in _index_names("knowledge_indices"):
            batch_op.create_index("ix_knowledge_indices_collection_id", ["collection_id"], unique=False)
        if "ix_knowledge_indices_latest_run_id" not in _index_names("knowledge_indices"):
            batch_op.create_index("ix_knowledge_indices_latest_run_id", ["latest_run_id"], unique=False)

    if not _table_exists("knowledge_index_runs"):
        op.create_table(
            "knowledge_index_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("knowledge_index_id", sa.String(), nullable=False),
            sa.Column("artifact_id", sa.String(), nullable=True),
            sa.Column("collection_id", sa.String(), nullable=True),
            sa.Column("profile_name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("source_path", sa.String(), nullable=True),
            sa.Column("output_dir", sa.String(), nullable=True),
            sa.Column("manifest_path", sa.String(), nullable=True),
            sa.Column("duration_ms", sa.Float(), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("run_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("started_at", sa.Float(), nullable=True),
            sa.Column("finished_at", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    with op.batch_alter_table("knowledge_index_runs", schema=None) as batch_op:
        if "ix_knowledge_index_runs_knowledge_index_id" not in _index_names("knowledge_index_runs"):
            batch_op.create_index("ix_knowledge_index_runs_knowledge_index_id", ["knowledge_index_id"], unique=False)
        if "ix_knowledge_index_runs_artifact_id" not in _index_names("knowledge_index_runs"):
            batch_op.create_index("ix_knowledge_index_runs_artifact_id", ["artifact_id"], unique=False)
        if "ix_knowledge_index_runs_collection_id" not in _index_names("knowledge_index_runs"):
            batch_op.create_index("ix_knowledge_index_runs_collection_id", ["collection_id"], unique=False)


def downgrade() -> None:
    if _table_exists("knowledge_index_runs"):
        with op.batch_alter_table("knowledge_index_runs", schema=None) as batch_op:
            for index_name in (
                "ix_knowledge_index_runs_collection_id",
                "ix_knowledge_index_runs_artifact_id",
                "ix_knowledge_index_runs_knowledge_index_id",
            ):
                if index_name in _index_names("knowledge_index_runs"):
                    batch_op.drop_index(index_name)
        op.drop_table("knowledge_index_runs")

    if _table_exists("knowledge_indices"):
        with op.batch_alter_table("knowledge_indices", schema=None) as batch_op:
            for index_name in (
                "ix_knowledge_indices_latest_run_id",
                "ix_knowledge_indices_collection_id",
                "ix_knowledge_indices_artifact_id",
            ):
                if index_name in _index_names("knowledge_indices"):
                    batch_op.drop_index(index_name)
        op.drop_table("knowledge_indices")
