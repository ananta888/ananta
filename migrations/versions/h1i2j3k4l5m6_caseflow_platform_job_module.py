"""CaseFlow Platform and Job Module tables

Revision ID: h1i2j3k4l5m6
Revises: g1h2i3j4k5l6
Create Date: 2026-06-29 00:00:00.000000

Creates all CaseFlow Platform tables:
- caseflow_cases
- caseflow_events
- caseflow_artifacts
- caseflow_actions
- discovery_profiles
- discovery_runs
- discovery_results
- case_agent_runs
- case_blueprint_bindings
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "h1i2j3k4l5m6"
down_revision: Union[str, Sequence[str], None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "caseflow_cases" not in existing:
        op.create_table(
            "caseflow_cases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("case_type", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="new"),
            sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
            sa.Column("risk", sa.String(), nullable=False, server_default="low"),
            sa.Column("owner", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("domain_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="0"),
        )

    if "caseflow_events" not in existing:
        op.create_table(
            "caseflow_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("case_id", sa.String(), nullable=False, index=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("actor_type", sa.String(), nullable=False, server_default="system"),
            sa.Column("actor_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("artifact_id", sa.String(), nullable=True),
        )
        op.create_index("ix_caseflow_events_case_id", "caseflow_events", ["case_id"])

    if "caseflow_artifacts" not in existing:
        op.create_table(
            "caseflow_artifacts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("case_id", sa.String(), nullable=False),
            sa.Column("artifact_type", sa.String(), nullable=False),
            sa.Column("artifact_kind", sa.String(), nullable=False, server_default="text"),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False, server_default="manual"),
            sa.Column("content_ref", sa.String(), nullable=True),
            sa.Column("content_text", sa.Text(), nullable=True),
            sa.Column("mime_type", sa.String(), nullable=False, server_default="text/plain"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("version_group_id", sa.String(), nullable=True),
            sa.Column("previous_artifact_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("agent_run_id", sa.String(), nullable=True),
            sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_caseflow_artifacts_case_id", "caseflow_artifacts", ["case_id"])

    if "caseflow_actions" not in existing:
        op.create_table(
            "caseflow_actions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("case_id", sa.String(), nullable=False),
            sa.Column("action_type", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="open"),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
            sa.Column("assigned_to", sa.String(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=False, server_default="system"),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("blocking", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_caseflow_actions_case_id", "caseflow_actions", ["case_id"])

    if "discovery_profiles" not in existing:
        op.create_table(
            "discovery_profiles",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("profile_type", sa.String(), nullable=False, server_default="job_search"),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if "discovery_runs" not in existing:
        op.create_table(
            "discovery_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("profile_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("errors_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("trace_id", sa.String(), nullable=True),
        )
        op.create_index("ix_discovery_runs_profile_id", "discovery_runs", ["profile_id"])

    if "discovery_results" not in existing:
        op.create_table(
            "discovery_results",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("result_type", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("source_url", sa.String(), nullable=True),
            sa.Column("source_name", sa.String(), nullable=False),
            sa.Column("raw_text", sa.Text(), nullable=True),
            sa.Column("normalized_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("fingerprint", sa.String(), nullable=True),
            sa.Column("duplicate_of", sa.String(), nullable=True),
            sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("ignored", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("converted_to_case_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_discovery_results_run_id", "discovery_results", ["run_id"])

    if "case_agent_runs" not in existing:
        op.create_table(
            "case_agent_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("case_id", sa.String(), nullable=False),
            sa.Column("agent_profile_id", sa.String(), nullable=False),
            sa.Column("input_artifact_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("output_artifact_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("model_profile_id", sa.String(), nullable=True),
            sa.Column("estimated_cost", sa.Float(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        )
        op.create_index("ix_case_agent_runs_case_id", "case_agent_runs", ["case_id"])

    if "case_blueprint_bindings" not in existing:
        op.create_table(
            "case_blueprint_bindings",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("case_id", sa.String(), nullable=False),
            sa.Column("visual_process_graph_id", sa.String(), nullable=False),
            sa.Column("blueprint_id", sa.String(), nullable=True),
            sa.Column("active_step_id", sa.String(), nullable=True),
            sa.Column("workflow_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        )
        op.create_index("ix_case_blueprint_bindings_case_id", "case_blueprint_bindings", ["case_id"])


def downgrade() -> None:
    existing = _existing_tables()
    for tbl in [
        "case_blueprint_bindings",
        "case_agent_runs",
        "discovery_results",
        "discovery_runs",
        "discovery_profiles",
        "caseflow_actions",
        "caseflow_artifacts",
        "caseflow_events",
        "caseflow_cases",
    ]:
        if tbl in existing:
            op.drop_table(tbl)
