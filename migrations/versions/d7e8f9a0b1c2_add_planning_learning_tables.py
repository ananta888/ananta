"""add planning learning tables

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-05-20 23:10:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planning_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("goal_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("goal_text_hash", sa.String(), nullable=True),
        sa.Column("goal_text_preview", sa.String(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False, server_default="generic"),
        sa.Column("mode_data", sa.JSON(), nullable=False),
        sa.Column("model_provider", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("model_base_url_hash", sa.String(), nullable=True),
        sa.Column("planning_profile", sa.String(), nullable=True),
        sa.Column("prompt_version_id", sa.String(), nullable=True),
        sa.Column("prompt_language", sa.String(), nullable=True),
        sa.Column("context_policy_ref", sa.String(), nullable=True),
        sa.Column("context_char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_output_ref", sa.String(), nullable=True),
        sa.Column("raw_output_preview", sa.String(), nullable=True),
        sa.Column("parse_mode", sa.String(), nullable=True),
        sa.Column("parse_confidence", sa.String(), nullable=True),
        sa.Column("parse_warnings", sa.JSON(), nullable=False),
        sa.Column("repair_needed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("repair_success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("repair_strategy_used", sa.String(), nullable=True),
        sa.Column("repair_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("generated_task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_artifacts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verification_spec_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dependency_mode_distribution", sa.JSON(), nullable=False),
        sa.Column("materialized_task_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="started"),
        sa.Column("error_classification", sa.String(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_runs_goal_id", "planning_runs", ["goal_id"])
    op.create_index("ix_planning_runs_trace_id", "planning_runs", ["trace_id"])
    op.create_index("ix_planning_runs_task_id", "planning_runs", ["task_id"])
    op.create_index("ix_planning_runs_mode", "planning_runs", ["mode"])
    op.create_index("ix_planning_runs_model_provider", "planning_runs", ["model_provider"])
    op.create_index("ix_planning_runs_model_name", "planning_runs", ["model_name"])
    op.create_index("ix_planning_runs_planning_profile", "planning_runs", ["planning_profile"])
    op.create_index("ix_planning_runs_prompt_version_id", "planning_runs", ["prompt_version_id"])
    op.create_index("ix_planning_runs_status", "planning_runs", ["status"])
    op.create_index("ix_planning_runs_created_at", "planning_runs", ["created_at"])
    op.create_index("ix_planning_runs_updated_at", "planning_runs", ["updated_at"])

    op.create_table(
        "planning_prompt_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False, server_default="de"),
        sa.Column("target_model_family", sa.String(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False, server_default="generic"),
        sa.Column("output_contract", sa.JSON(), nullable=False),
        sa.Column("system_rules", sa.JSON(), nullable=False),
        sa.Column("user_prompt_template", sa.String(), nullable=False),
        sa.Column("repair_prompt_template", sa.String(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_prompt_versions_version", "planning_prompt_versions", ["version"])
    op.create_index("ix_planning_prompt_versions_language", "planning_prompt_versions", ["language"])
    op.create_index("ix_planning_prompt_versions_target_model_family", "planning_prompt_versions", ["target_model_family"])
    op.create_index("ix_planning_prompt_versions_mode", "planning_prompt_versions", ["mode"])
    op.create_index("ix_planning_prompt_versions_checksum", "planning_prompt_versions", ["checksum"])

    op.create_table(
        "planning_model_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_name_pattern", sa.String(), nullable=True),
        sa.Column("model_family", sa.String(), nullable=True),
        sa.Column("profile_name", sa.String(), nullable=False),
        sa.Column("prompt_language", sa.String(), nullable=False, server_default="de"),
        sa.Column("context_max_chars", sa.Integer(), nullable=False, server_default="1200"),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("repair_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("repair_strategies", sa.JSON(), nullable=False),
        sa.Column("preferred_prompt_version_id", sa.String(), nullable=True),
        sa.Column("output_contract_strictness", sa.String(), nullable=False, server_default="repair_required"),
        sa.Column("supports_json_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_english_prompt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_model_profiles_provider", "planning_model_profiles", ["provider"])
    op.create_index("ix_planning_model_profiles_model_name_pattern", "planning_model_profiles", ["model_name_pattern"])
    op.create_index("ix_planning_model_profiles_model_family", "planning_model_profiles", ["model_family"])
    op.create_index("ix_planning_model_profiles_profile_name", "planning_model_profiles", ["profile_name"])

    op.create_table(
        "planning_evaluations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("planning_run_id", sa.String(), nullable=True),
        sa.Column("goal_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("parse_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("validation_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("materialization_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("execution_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("artifact_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("verification_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("completion_status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_evaluations_planning_run_id", "planning_evaluations", ["planning_run_id"])
    op.create_index("ix_planning_evaluations_goal_id", "planning_evaluations", ["goal_id"])
    op.create_index("ix_planning_evaluations_trace_id", "planning_evaluations", ["trace_id"])
    op.create_index("ix_planning_evaluations_completion_status", "planning_evaluations", ["completion_status"])

    op.create_table(
        "planning_template_candidates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("goal_type", sa.String(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False, server_default="generic"),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False, server_default="low"),
        sa.Column("status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_template_candidates_source_run_id", "planning_template_candidates", ["source_run_id"])
    op.create_index("ix_planning_template_candidates_goal_type", "planning_template_candidates", ["goal_type"])
    op.create_index("ix_planning_template_candidates_mode", "planning_template_candidates", ["mode"])
    op.create_index("ix_planning_template_candidates_confidence", "planning_template_candidates", ["confidence"])
    op.create_index("ix_planning_template_candidates_status", "planning_template_candidates", ["status"])

    op.create_table(
        "planning_pattern_clusters",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("goal_type", sa.String(), nullable=True),
        sa.Column("model_provider", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("cluster_key", sa.String(), nullable=False),
        sa.Column("cluster_payload", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_pattern_clusters_goal_type", "planning_pattern_clusters", ["goal_type"])
    op.create_index("ix_planning_pattern_clusters_model_provider", "planning_pattern_clusters", ["model_provider"])
    op.create_index("ix_planning_pattern_clusters_model_name", "planning_pattern_clusters", ["model_name"])
    op.create_index("ix_planning_pattern_clusters_cluster_key", "planning_pattern_clusters", ["cluster_key"])

    op.create_table(
        "planning_review_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("planning_run_id", sa.String(), nullable=False),
        sa.Column("review_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("action_log", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_review_items_planning_run_id", "planning_review_items", ["planning_run_id"])
    op.create_index("ix_planning_review_items_review_type", "planning_review_items", ["review_type"])
    op.create_index("ix_planning_review_items_status", "planning_review_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_planning_review_items_status", table_name="planning_review_items")
    op.drop_index("ix_planning_review_items_review_type", table_name="planning_review_items")
    op.drop_index("ix_planning_review_items_planning_run_id", table_name="planning_review_items")
    op.drop_table("planning_review_items")

    op.drop_index("ix_planning_pattern_clusters_cluster_key", table_name="planning_pattern_clusters")
    op.drop_index("ix_planning_pattern_clusters_model_name", table_name="planning_pattern_clusters")
    op.drop_index("ix_planning_pattern_clusters_model_provider", table_name="planning_pattern_clusters")
    op.drop_index("ix_planning_pattern_clusters_goal_type", table_name="planning_pattern_clusters")
    op.drop_table("planning_pattern_clusters")

    op.drop_index("ix_planning_template_candidates_status", table_name="planning_template_candidates")
    op.drop_index("ix_planning_template_candidates_confidence", table_name="planning_template_candidates")
    op.drop_index("ix_planning_template_candidates_mode", table_name="planning_template_candidates")
    op.drop_index("ix_planning_template_candidates_goal_type", table_name="planning_template_candidates")
    op.drop_index("ix_planning_template_candidates_source_run_id", table_name="planning_template_candidates")
    op.drop_table("planning_template_candidates")

    op.drop_index("ix_planning_evaluations_completion_status", table_name="planning_evaluations")
    op.drop_index("ix_planning_evaluations_trace_id", table_name="planning_evaluations")
    op.drop_index("ix_planning_evaluations_goal_id", table_name="planning_evaluations")
    op.drop_index("ix_planning_evaluations_planning_run_id", table_name="planning_evaluations")
    op.drop_table("planning_evaluations")

    op.drop_index("ix_planning_model_profiles_profile_name", table_name="planning_model_profiles")
    op.drop_index("ix_planning_model_profiles_model_family", table_name="planning_model_profiles")
    op.drop_index("ix_planning_model_profiles_model_name_pattern", table_name="planning_model_profiles")
    op.drop_index("ix_planning_model_profiles_provider", table_name="planning_model_profiles")
    op.drop_table("planning_model_profiles")

    op.drop_index("ix_planning_prompt_versions_checksum", table_name="planning_prompt_versions")
    op.drop_index("ix_planning_prompt_versions_mode", table_name="planning_prompt_versions")
    op.drop_index("ix_planning_prompt_versions_target_model_family", table_name="planning_prompt_versions")
    op.drop_index("ix_planning_prompt_versions_language", table_name="planning_prompt_versions")
    op.drop_index("ix_planning_prompt_versions_version", table_name="planning_prompt_versions")
    op.drop_table("planning_prompt_versions")

    op.drop_index("ix_planning_runs_updated_at", table_name="planning_runs")
    op.drop_index("ix_planning_runs_created_at", table_name="planning_runs")
    op.drop_index("ix_planning_runs_status", table_name="planning_runs")
    op.drop_index("ix_planning_runs_prompt_version_id", table_name="planning_runs")
    op.drop_index("ix_planning_runs_planning_profile", table_name="planning_runs")
    op.drop_index("ix_planning_runs_model_name", table_name="planning_runs")
    op.drop_index("ix_planning_runs_model_provider", table_name="planning_runs")
    op.drop_index("ix_planning_runs_mode", table_name="planning_runs")
    op.drop_index("ix_planning_runs_task_id", table_name="planning_runs")
    op.drop_index("ix_planning_runs_trace_id", table_name="planning_runs")
    op.drop_index("ix_planning_runs_goal_id", table_name="planning_runs")
    op.drop_table("planning_runs")
