"""Static DDL contracts for the enterprise-organization migration chain.

These tests compile SQL only. They deliberately do not execute Alembic or open
a database connection, so they can guard historical migration immutability and
schema shape independently of runtime migration tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from agent.db_models.planning import TemplateDB
from agent.db_models.tasks import ArchivedTaskDB, TaskDB
from agent.db_models.teams import TeamBlueprintDB
from migrations.enterprise_organization_schema_v1 import (
    ENTERPRISE_ORGANIZATION_TABLE_NAMES,
    enterprise_organization_tables_v1,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORE_MIGRATION = REPOSITORY_ROOT / "migrations/versions/3e8c0f2a4b6d_add_enterprise_organization_core.py"
PLANNING_MIGRATION = REPOSITORY_ROOT / "migrations/versions/4f9d1a3b5c7e_add_planning_control_plane.py"
RUNTIME_MIGRATION = REPOSITORY_ROOT / "migrations/versions/5a0e2b4c6d8f_add_organization_runtime_ledgers.py"
MIGRATION_FILES = (CORE_MIGRATION, PLANNING_MIGRATION, RUNTIME_MIGRATION)

CORE_TABLES = (
    "role_template_revisions",
    "workflow_definition_revisions",
    "team_blueprint_revisions",
    "organization_limit_profile_revisions",
    "organization_policy_revisions",
    "organization_blueprint_revisions",
    "organization_handoff_definition_revisions",
    "organization_instances",
    "organization_units",
    "organization_team_links",
    "organization_role_slots",
    "organization_role_assignments",
    "organization_relations",
    "organization_memberships",
    "organization_admin_grants",
    "organization_topology_patch_grants",
    "organization_admission_exceptions",
    "organization_layout_preferences",
    "organization_topology_snapshots",
    "organization_operations",
    "organization_audit_outbox",
    "cross_team_task_dependencies",
)
PLANNING_TABLES = (
    "planning_artifact_revisions",
    "planning_lineage",
    "planning_operation_receipts",
    "planning_task_mappings",
    "planning_amendment_inputs",
    "planning_task_dispatches",
    "worker_task_proposals",
)
RUNTIME_TABLES = (
    "organization_budget_usage",
    "organization_budget_reservations",
    "organization_runtime_events",
    "organization_team_handoffs",
    "organization_workflow_loop_states",
)

EXPECTED_SCOPED_FOREIGN_KEYS = {
    "organization_audit_outbox": {
        "fk_organization_audit_outbox_project": (
            ("tenant_id", "project_id"),
            "projects",
            ("tenant_id", "project_id"),
            "CASCADE",
        ),
    },
    "cross_team_task_dependencies": {
        "fk_cross_team_dependency_source_task": (
            ("tenant_id", "project_id", "organization_id", "source_task_id"),
            "tasks",
            ("tenant_id", "project_id", "organization_id", "id"),
            "CASCADE",
        ),
        "fk_cross_team_dependency_target_task": (
            ("tenant_id", "project_id", "organization_id", "target_task_id"),
            "tasks",
            ("tenant_id", "project_id", "organization_id", "id"),
            "CASCADE",
        ),
        "fk_cross_team_dependency_source_team": (
            ("tenant_id", "project_id", "organization_id", "source_team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "RESTRICT",
        ),
        "fk_cross_team_dependency_target_team": (
            ("tenant_id", "project_id", "organization_id", "target_team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "RESTRICT",
        ),
    },
    "organization_budget_reservations": {
        "fk_organization_budget_reservations_unit": (
            ("tenant_id", "project_id", "organization_id", "unit_id"),
            "organization_units",
            ("tenant_id", "project_id", "organization_id", "id"),
            "RESTRICT",
        ),
        "fk_organization_budget_reservations_team": (
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "RESTRICT",
        ),
    },
    "organization_team_handoffs": {
        "fk_organization_team_handoffs_goal": (
            ("tenant_id", "project_id", "goal_id"),
            "goals",
            ("tenant_id", "project_id", "id"),
            "RESTRICT",
        ),
        "fk_organization_team_handoffs_producer_unit": (
            ("tenant_id", "project_id", "organization_id", "producer_unit_id"),
            "organization_units",
            ("tenant_id", "project_id", "organization_id", "id"),
            "RESTRICT",
        ),
        "fk_organization_team_handoffs_consumer_unit": (
            ("tenant_id", "project_id", "organization_id", "consumer_unit_id"),
            "organization_units",
            ("tenant_id", "project_id", "organization_id", "id"),
            "RESTRICT",
        ),
        "fk_organization_team_handoffs_producer_team": (
            ("tenant_id", "project_id", "organization_id", "producer_team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "RESTRICT",
        ),
        "fk_organization_team_handoffs_consumer_team": (
            ("tenant_id", "project_id", "organization_id", "consumer_team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "RESTRICT",
        ),
        "fk_organization_team_handoffs_producer_role_slot": (
            ("tenant_id", "project_id", "organization_id", "producer_role_slot_id"),
            "organization_role_slots",
            ("tenant_id", "project_id", "organization_id", "id"),
            "RESTRICT",
        ),
        "fk_organization_team_handoffs_consumer_role_slot": (
            ("tenant_id", "project_id", "organization_id", "consumer_role_slot_id"),
            "organization_role_slots",
            ("tenant_id", "project_id", "organization_id", "id"),
            "RESTRICT",
        ),
    },
    "organization_workflow_loop_states": {
        "fk_organization_workflow_loops_unit": (
            ("tenant_id", "project_id", "organization_id", "unit_id"),
            "organization_units",
            ("tenant_id", "project_id", "organization_id", "id"),
            "RESTRICT",
        ),
        "fk_organization_workflow_loops_team": (
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "RESTRICT",
        ),
    },
}

EXPECTED_CHECKS = {
    "ck_role_template_revision_version",
    "ck_team_blueprint_revision_versions",
    "ck_workflow_definition_revision_version",
    "ck_organization_limit_profile_lifecycle",
    "ck_organization_policy_revision_revision",
    "ck_organization_blueprint_revision_version",
    "ck_organization_handoff_definition_lifecycle",
    "ck_organization_handoff_definition_version",
    "ck_organization_instance_definition_versions",
    "ck_organization_unit_team_blueprint_version",
    "ck_organization_unit_group_ordinal",
    "ck_organization_role_slot_template_version",
    "ck_organization_role_slot_lifecycle",
    "ck_organization_relation_handoff_version",
    "ck_organization_relation_dependency_policy",
    "ck_organization_relation_lifecycle",
    "ck_organization_admission_exception_values",
    "ck_organization_topology_snapshot_revision",
    "ck_planning_artifact_revision",
    "ck_planning_operation_receipt_status",
    "ck_worker_task_proposal_state",
    "ck_worker_task_proposal_values",
    "ck_planning_amendment_input_revision",
    "ck_planning_amendment_input_state",
    "ck_planning_task_dispatch_attempt",
    "ck_planning_task_dispatch_status",
    "ck_organization_budget_reservation_actual_values",
    "ck_organization_budget_reservation_settlement",
    "ck_organization_team_handoff_sla",
}


def _foreign_key_signature(constraint: sa.ForeignKeyConstraint) -> tuple:
    elements = tuple(constraint.elements)
    return (
        tuple(element.parent.name for element in elements),
        elements[0].column.table.name,
        tuple(element.column.name for element in elements),
        constraint.ondelete,
    )


def _top_level_call_names(function: ast.FunctionDef) -> list[str]:
    names: list[str] = []
    for statement in function.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        function_node = statement.value.func
        if isinstance(function_node, ast.Name):
            names.append(function_node.id)
        elif isinstance(function_node, ast.Attribute):
            names.append(function_node.attr)
    return names


def test_frozen_snapshot_covers_each_revision_table_exactly_once() -> None:
    assert ENTERPRISE_ORGANIZATION_TABLE_NAMES == CORE_TABLES + PLANNING_TABLES + RUNTIME_TABLES
    assert len(ENTERPRISE_ORGANIZATION_TABLE_NAMES) == len(set(ENTERPRISE_ORGANIZATION_TABLE_NAMES))
    assert tuple(enterprise_organization_tables_v1()) == ENTERPRISE_ORGANIZATION_TABLE_NAMES


@pytest.mark.parametrize("path", MIGRATION_FILES, ids=lambda path: path.stem)
def test_historical_revisions_do_not_import_live_model_metadata(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert "agent.db_models" not in source
    assert "SQLModel.metadata" not in source
    assert "enterprise_organization_tables_v1" in source


@pytest.mark.parametrize(
    "dialect",
    (sqlite.dialect(), postgresql.dialect()),
    ids=("sqlite", "postgresql"),
)
def test_frozen_schema_compiles_for_supported_dialects(dialect: sa.engine.Dialect) -> None:
    tables = enterprise_organization_tables_v1()
    for table in tables.values():
        assert str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))


def test_scope_sensitive_foreign_keys_are_composite() -> None:
    tables = enterprise_organization_tables_v1()
    for table_name, expected_constraints in EXPECTED_SCOPED_FOREIGN_KEYS.items():
        actual = {
            constraint.name: _foreign_key_signature(constraint)
            for constraint in tables[table_name].foreign_key_constraints
        }
        for constraint_name, signature in expected_constraints.items():
            assert actual[constraint_name] == signature


def test_database_checks_cover_model_validation_invariants() -> None:
    actual = {
        constraint.name
        for table in enterprise_organization_tables_v1().values()
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert EXPECTED_CHECKS <= actual


def test_legacy_json_defaults_match_additive_migration_contract() -> None:
    template_columns = TemplateDB.__table__.c
    blueprint_columns = TeamBlueprintDB.__table__.c
    expected = {
        template_columns.appendix_refs: "'[]'",
        template_columns.template_metadata: "'{}'",
        blueprint_columns.workflow_checks: "'{}'",
        blueprint_columns.workflow_required_capabilities: "'[]'",
    }
    for column, server_default in expected.items():
        assert column.nullable is False
        assert column.server_default is not None
        assert str(column.server_default.arg) == server_default
    assert str(blueprint_columns.workflow_mode.server_default.arg) == "gated"
    assert str(blueprint_columns.workflow_default_failure_policy.server_default.arg) == "manual"


def test_runtime_binding_index_model_and_downgrade_parity() -> None:
    task_indexes = {index.name for index in TaskDB.__table__.indexes}
    archived_indexes = {index.name for index in ArchivedTaskDB.__table__.indexes}
    assert "ix_tasks_team_id" in task_indexes
    assert {
        "ix_archived_tasks_team_id",
        "ix_archived_tasks_goal_id",
        "ix_archived_tasks_plan_id",
        "ix_archived_tasks_plan_node_id",
    } <= archived_indexes

    source = CORE_MIGRATION.read_text(encoding="utf-8")
    for index_name in task_indexes | archived_indexes:
        if index_name in {
            "ix_tasks_team_id",
            "ix_archived_tasks_team_id",
            "ix_archived_tasks_goal_id",
            "ix_archived_tasks_plan_id",
            "ix_archived_tasks_plan_node_id",
        }:
            assert index_name in source


def test_core_downgrade_drops_children_before_parents() -> None:
    tree = ast.parse(CORE_MIGRATION.read_text(encoding="utf-8"))
    downgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade")
    assert _top_level_call_names(downgrade) == [
        "_drop_organization_tables",
        "_drop_runtime_binding_constraints",
        "_drop_runtime_binding_indexes",
        "_drop_runtime_binding_columns",
        "_drop_organization_tables",
        "_drop_legacy_definition_columns",
    ]


def test_planning_downgrade_drops_children_before_approval_parent() -> None:
    tree = ast.parse(PLANNING_MIGRATION.read_text(encoding="utf-8"))
    downgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade")
    approval_drop = next(
        statement
        for statement in downgrade.body
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "_drop_approval_request_extensions"
    )
    table_drop = next(
        node
        for node in ast.walk(downgrade)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "drop"
    )
    assert table_drop.lineno < approval_drop.lineno
