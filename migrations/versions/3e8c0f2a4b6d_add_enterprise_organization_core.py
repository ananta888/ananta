"""Add normalized enterprise organization control-plane persistence.

Revision ID: 3e8c0f2a4b6d
Revises: 2f7b9d1e3a5c
"""

from __future__ import annotations

import hashlib
import json
import re

import sqlalchemy as sa
from alembic import op

from migrations.enterprise_organization_schema_v1 import enterprise_organization_tables_v1

revision = "3e8c0f2a4b6d"
down_revision = "2f7b9d1e3a5c"
branch_labels = None
depends_on = None


ORGANIZATION_TABLE_ORDER = (
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

LEGACY_DEFINITION_DEPENDENT_TABLES = ("team_blueprint_revisions",)
RUNTIME_BINDING_DEPENDENT_TABLES = ("cross_team_task_dependencies",)
DEFERRED_ORGANIZATION_TABLES = LEGACY_DEFINITION_DEPENDENT_TABLES + RUNTIME_BINDING_DEPENDENT_TABLES

# Columns the downgrade removes, per table.  SQLite rebuilds a table to drop a
# column and recreates its indexes on the new table, so an index still pointing
# at a dropped column fails that rebuild — which is how the downgrade broke on
# ix_goals_goal_kind.  Both the drop list and the index list come from these
# tuples, so an index can no longer outlive its own column.
RUNTIME_BINDING_COLUMNS = {
    "goals": ("organization_id", "unit_id", "goal_kind", "parent_goal_id"),
    "plan_nodes": (
        "tenant_id",
        "project_id",
        "organization_id",
        "unit_id",
        "team_id",
        "role_slot_id",
    ),
    "tasks": ("organization_id", "unit_id", "role_slot_id"),
    "archived_tasks": (
        "tenant_id",
        "project_id",
        "organization_id",
        "unit_id",
        "role_slot_id",
    ),
}

LEGACY_DEFINITION_COLUMNS = {
    "templates": (
        "definition_key",
        "definition_version",
        "definition_hash",
        "prompt_hash",
        "appendix_refs",
        "template_metadata",
        "definition_lifecycle",
    ),
    "team_blueprints": (
        "definition_key",
        "definition_version",
        "definition_hash",
        "definition_lifecycle",
        "workflow_definition_key",
        "workflow_definition_version",
        "workflow_mode",
        "workflow_default_failure_policy",
        "workflow_checks",
        "workflow_required_capabilities",
    ),
}


def _index_names_for_dropped_columns(spec: dict) -> dict:
    return {
        table_name: tuple(f"ix_{table_name}_{column}" for column in columns)
        for table_name, columns in spec.items()
    }


# Indexes this migration adds on columns that survive the downgrade.  They are
# still its own additions, so it still removes them; they cannot be derived
# from the dropped columns and are named explicitly.
RETAINED_COLUMN_INDEXES = {
    "tasks": ("ix_tasks_team_id",),
    "archived_tasks": (
        "ix_archived_tasks_team_id",
        "ix_archived_tasks_goal_id",
        "ix_archived_tasks_plan_id",
        "ix_archived_tasks_plan_node_id",
    ),
}


def _merge_index_names(*specs: dict) -> dict:
    merged: dict[str, tuple[str, ...]] = {}
    for spec in specs:
        for table_name, names in spec.items():
            merged[table_name] = tuple(dict.fromkeys(merged.get(table_name, ()) + tuple(names)))
    return merged


RUNTIME_BINDING_INDEXES = _merge_index_names(
    _index_names_for_dropped_columns(RUNTIME_BINDING_COLUMNS),
    RETAINED_COLUMN_INDEXES,
)
LEGACY_DEFINITION_INDEXES = _index_names_for_dropped_columns(LEGACY_DEFINITION_COLUMNS)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _organization_tables()
    initial_tables = tuple(name for name in ORGANIZATION_TABLE_ORDER if name not in DEFERRED_ORGANIZATION_TABLES)
    _create_organization_tables(bind, tables, initial_tables)
    _extend_legacy_definition_tables(bind)
    _create_organization_tables(bind, tables, LEGACY_DEFINITION_DEPENDENT_TABLES)
    _add_runtime_binding_columns()
    _backfill_runtime_binding_scopes(bind)
    _create_runtime_reference_uniques()
    _sqlite_orphan_preflight(bind)
    _create_runtime_binding_constraints()
    # Cross-team dependencies use the scoped task identity above and must not
    # exist while SQLite batch-recreates their parent tasks table.
    _create_organization_tables(bind, tables, RUNTIME_BINDING_DEPENDENT_TABLES)
    _backfill_legacy_definition_identities(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _organization_tables()
    _drop_organization_tables(bind, tables, RUNTIME_BINDING_DEPENDENT_TABLES)
    _drop_runtime_binding_constraints(bind)
    _drop_runtime_binding_indexes(bind)
    _drop_runtime_binding_columns(bind)
    remaining_tables = tuple(name for name in ORGANIZATION_TABLE_ORDER if name not in RUNTIME_BINDING_DEPENDENT_TABLES)
    _drop_organization_tables(bind, tables, remaining_tables)
    # team_blueprint_revisions references the legacy table, so its child table
    # must be gone before SQLite batch-recreates team_blueprints.
    _drop_legacy_definition_columns(bind)


def _organization_tables() -> dict[str, sa.Table]:
    tables = enterprise_organization_tables_v1()
    return {name: tables[name] for name in ORGANIZATION_TABLE_ORDER}


def _create_organization_tables(
    bind,
    tables: dict[str, sa.Table],
    table_names: tuple[str, ...],
) -> None:
    existing = set(sa.inspect(bind).get_table_names())
    for name in table_names:
        if name not in existing:
            tables[name].create(bind=bind, checkfirst=True)
            existing.add(name)


def _drop_organization_tables(
    bind,
    tables: dict[str, sa.Table],
    table_names: tuple[str, ...],
) -> None:
    existing = set(sa.inspect(bind).get_table_names())
    for name in reversed(table_names):
        if name in existing:
            tables[name].drop(bind=bind, checkfirst=True)
            existing.remove(name)


def _extend_legacy_definition_tables(bind) -> None:
    _add_columns(
        "templates",
        [
            sa.Column("definition_key", sa.String(191), nullable=True),
            sa.Column("definition_version", sa.Integer(), nullable=True),
            sa.Column("definition_hash", sa.String(64), nullable=True),
            sa.Column("prompt_hash", sa.String(64), nullable=True),
            sa.Column("appendix_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("template_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("definition_lifecycle", sa.String(16), nullable=True),
        ],
    )
    _ensure_index("templates", "ix_templates_definition_key", ["definition_key"])
    _ensure_index("templates", "ix_templates_definition_hash", ["definition_hash"])
    _ensure_unique("templates", "uq_templates_definition_key_version", ["definition_key", "definition_version"])

    _add_columns(
        "team_blueprints",
        [
            sa.Column("definition_key", sa.String(191), nullable=True),
            sa.Column("definition_version", sa.Integer(), nullable=True),
            sa.Column("definition_hash", sa.String(64), nullable=True),
            sa.Column("definition_lifecycle", sa.String(16), nullable=True),
            sa.Column("workflow_definition_key", sa.String(191), nullable=True),
            sa.Column("workflow_definition_version", sa.Integer(), nullable=True),
            sa.Column("workflow_mode", sa.String(64), nullable=False, server_default="gated"),
            sa.Column("workflow_default_failure_policy", sa.String(64), nullable=False, server_default="manual"),
            sa.Column("workflow_checks", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("workflow_required_capabilities", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        ],
    )
    _ensure_index("team_blueprints", "ix_team_blueprints_definition_key", ["definition_key"])
    _ensure_index("team_blueprints", "ix_team_blueprints_definition_hash", ["definition_hash"])
    _ensure_unique(
        "team_blueprints",
        "uq_team_blueprints_definition_key_version",
        ["definition_key", "definition_version"],
    )


def _add_runtime_binding_columns() -> None:
    _add_columns(
        "goals",
        [
            sa.Column("organization_id", sa.String(191), nullable=True),
            sa.Column("unit_id", sa.String(191), nullable=True),
            sa.Column("goal_kind", sa.String(32), nullable=True),
            sa.Column("parent_goal_id", sa.String(), nullable=True),
        ],
    )
    _add_columns(
        "plan_nodes",
        [
            sa.Column("tenant_id", sa.String(191), nullable=True),
            sa.Column("project_id", sa.String(191), nullable=True),
            sa.Column("organization_id", sa.String(191), nullable=True),
            sa.Column("unit_id", sa.String(191), nullable=True),
            sa.Column("team_id", sa.String(), nullable=True),
            sa.Column("role_slot_id", sa.String(191), nullable=True),
        ],
    )
    _add_columns(
        "tasks",
        [
            sa.Column("organization_id", sa.String(191), nullable=True),
            sa.Column("unit_id", sa.String(191), nullable=True),
            sa.Column("role_slot_id", sa.String(191), nullable=True),
        ],
    )
    _add_columns(
        "archived_tasks",
        [
            sa.Column("tenant_id", sa.String(191), nullable=True),
            sa.Column("project_id", sa.String(191), nullable=True),
            sa.Column("organization_id", sa.String(191), nullable=True),
            sa.Column("unit_id", sa.String(191), nullable=True),
            sa.Column("role_slot_id", sa.String(191), nullable=True),
        ],
    )


def _backfill_runtime_binding_scopes(bind) -> None:
    """Recover only unambiguous legacy scopes before composite FKs exist."""

    goal_scopes = {
        str(row["id"]): (
            row["tenant_id"],
            row["project_id"],
            row["organization_id"],
        )
        for row in bind.execute(
            sa.text("SELECT id, tenant_id, project_id, organization_id FROM goals ORDER BY id")
        ).mappings()
    }
    organization_scopes = {
        str(row["organization_id"]): (
            row["tenant_id"],
            row["project_id"],
            row["organization_id"],
        )
        for row in bind.execute(
            sa.text(
                "SELECT organization_id, tenant_id, project_id FROM organization_instances ORDER BY organization_id"
            )
        ).mappings()
    }
    plan_goal_ids = {
        str(row["id"]): str(row["goal_id"])
        for row in bind.execute(sa.text("SELECT id, goal_id FROM plans ORDER BY id")).mappings()
    }
    _backfill_scoped_rows(
        bind,
        table_name="plan_nodes",
        rows=bind.execute(
            sa.text("SELECT id, tenant_id, project_id, organization_id, plan_id FROM plan_nodes ORDER BY id")
        ).mappings(),
        candidate_resolver=lambda row: (
            goal_scopes.get(plan_goal_ids.get(str(row["plan_id"]), "")),
            organization_scopes.get(str(row["organization_id"] or "")),
        ),
    )

    plan_node_scopes = {
        str(row["id"]): (
            row["tenant_id"],
            row["project_id"],
            row["organization_id"],
        )
        for row in bind.execute(
            sa.text("SELECT id, tenant_id, project_id, organization_id FROM plan_nodes ORDER BY id")
        ).mappings()
    }
    team_scopes: dict[str, list[tuple[object, object, object]]] = {}
    for row in bind.execute(
        sa.text(
            "SELECT team_id, tenant_id, project_id, organization_id "
            "FROM organization_team_links ORDER BY team_id, tenant_id, project_id, organization_id"
        )
    ).mappings():
        team_scopes.setdefault(str(row["team_id"]), []).append(
            (row["tenant_id"], row["project_id"], row["organization_id"])
        )
    for row in bind.execute(
        sa.text(
            "SELECT team_id, tenant_id, project_id "
            "FROM projects WHERE team_id IS NOT NULL ORDER BY team_id, tenant_id, project_id"
        )
    ).mappings():
        team_scopes.setdefault(str(row["team_id"]), []).append((row["tenant_id"], row["project_id"], None))

    def task_scope_candidates(row):
        return (
            goal_scopes.get(str(row["goal_id"] or "")),
            plan_node_scopes.get(str(row["plan_node_id"] or "")),
            organization_scopes.get(str(row["organization_id"] or "")),
            *team_scopes.get(str(row["team_id"] or ""), []),
        )

    _backfill_scoped_rows(
        bind,
        table_name="tasks",
        rows=_runtime_task_scope_rows(bind, "tasks"),
        candidate_resolver=task_scope_candidates,
    )
    active_task_scopes = {
        str(row["id"]): (
            row["tenant_id"],
            row["project_id"],
            row["organization_id"],
        )
        for row in bind.execute(
            sa.text("SELECT id, tenant_id, project_id, organization_id FROM tasks ORDER BY id")
        ).mappings()
    }
    _backfill_scoped_rows(
        bind,
        table_name="archived_tasks",
        rows=_runtime_task_scope_rows(bind, "archived_tasks"),
        candidate_resolver=lambda row: (
            *task_scope_candidates(row),
            active_task_scopes.get(str(row["id"])),
        ),
    )


def _runtime_task_scope_rows(bind, table_name: str):
    return bind.execute(
        sa.text(
            f"SELECT id, tenant_id, project_id, organization_id, "
            f"goal_id, plan_node_id, team_id FROM {table_name} ORDER BY id"
        )
    ).mappings()


def _backfill_scoped_rows(
    bind,
    *,
    table_name: str,
    rows,
    candidate_resolver,
) -> None:
    for row in rows:
        candidates = [
            candidate
            for candidate in (
                (row["tenant_id"], row["project_id"], row["organization_id"]),
                *candidate_resolver(row),
            )
            if candidate is not None and candidate[0] is not None and candidate[1] is not None
        ]
        scopes = {(str(value[0]), str(value[1])) for value in candidates}
        if len(scopes) > 1:
            raise RuntimeError(f"organization_runtime_scope_backfill_conflict:{table_name}:{row['id']}")
        if not scopes:
            # Intentionally preserve truly unscoped legacy rows.
            continue
        tenant_id, project_id = next(iter(scopes))
        organizations = {str(value[2]) for value in candidates if value[2] is not None}
        if len(organizations) > 1:
            raise RuntimeError(f"organization_runtime_organization_backfill_conflict:{table_name}:{row['id']}")
        organization_id = next(iter(organizations), None)
        bind.execute(
            sa.text(
                f"UPDATE {table_name} SET tenant_id=:tenant_id, project_id=:project_id, "
                "organization_id=COALESCE(organization_id, :organization_id) WHERE id=:id"
            ),
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "organization_id": organization_id,
                "id": row["id"],
            },
        )


def _create_runtime_reference_uniques() -> None:
    _ensure_unique(
        "goals",
        "uq_goals_project_scope_id",
        ["tenant_id", "project_id", "id"],
    )
    _ensure_unique(
        "goals",
        "uq_goals_organization_scope_id",
        ["tenant_id", "project_id", "organization_id", "id"],
    )
    _ensure_unique(
        "plan_nodes",
        "uq_plan_nodes_organization_scope_id",
        ["tenant_id", "project_id", "organization_id", "id"],
    )
    for table_name in ("tasks", "archived_tasks"):
        _ensure_unique(
            table_name,
            f"uq_{table_name}_organization_scope_id",
            ["tenant_id", "project_id", "organization_id", "id"],
        )


def _create_runtime_binding_constraints() -> None:
    _ensure_fk(
        "goals",
        "fk_goals_project_scope",
        ["tenant_id", "project_id"],
        "projects",
        ["tenant_id", "project_id"],
    )
    _ensure_fk(
        "goals",
        "fk_goals_organization_scope",
        ["tenant_id", "project_id", "organization_id"],
        "organization_instances",
        ["tenant_id", "project_id", "organization_id"],
    )
    _ensure_fk(
        "goals",
        "fk_goals_unit_scope",
        ["tenant_id", "project_id", "organization_id", "unit_id"],
        "organization_units",
        ["tenant_id", "project_id", "organization_id", "id"],
    )
    _ensure_fk(
        "goals",
        "fk_goals_organization_team_scope",
        ["tenant_id", "project_id", "organization_id", "team_id"],
        "organization_team_links",
        ["tenant_id", "project_id", "organization_id", "team_id"],
    )
    _ensure_fk(
        "goals",
        "fk_goals_parent_goal_id",
        ["parent_goal_id"],
        "goals",
        ["id"],
    )
    _ensure_fk(
        "goals",
        "fk_goals_parent_scope",
        ["tenant_id", "project_id", "parent_goal_id"],
        "goals",
        ["tenant_id", "project_id", "id"],
    )
    for column in RUNTIME_BINDING_COLUMNS["goals"]:
        _ensure_index("goals", f"ix_goals_{column}", [column])

    _ensure_fk("plans", "fk_plans_goal_id", ["goal_id"], "goals", ["id"])
    _ensure_fk("plan_nodes", "fk_plan_nodes_plan_id", ["plan_id"], "plans", ["id"])
    _binding_fks_and_indexes("plan_nodes", include_plan_node=False)
    for table_name in ("tasks", "archived_tasks"):
        _binding_fks_and_indexes(table_name, include_plan_node=True)


def _binding_fks_and_indexes(table_name: str, *, include_plan_node: bool) -> None:
    _ensure_fk(
        table_name,
        f"fk_{table_name}_project_scope",
        ["tenant_id", "project_id"],
        "projects",
        ["tenant_id", "project_id"],
    )
    _ensure_fk(
        table_name,
        f"fk_{table_name}_organization_scope",
        ["tenant_id", "project_id", "organization_id"],
        "organization_instances",
        ["tenant_id", "project_id", "organization_id"],
    )
    _ensure_fk(
        table_name,
        f"fk_{table_name}_unit_scope",
        ["tenant_id", "project_id", "organization_id", "unit_id"],
        "organization_units",
        ["tenant_id", "project_id", "organization_id", "id"],
    )
    _ensure_fk(
        table_name,
        f"fk_{table_name}_organization_team_scope",
        ["tenant_id", "project_id", "organization_id", "team_id"],
        "organization_team_links",
        ["tenant_id", "project_id", "organization_id", "team_id"],
    )
    _ensure_fk(
        table_name,
        f"fk_{table_name}_role_slot_scope",
        ["tenant_id", "project_id", "organization_id", "role_slot_id"],
        "organization_role_slots",
        ["tenant_id", "project_id", "organization_id", "id"],
    )
    if table_name in {"plan_nodes", "archived_tasks"}:
        _ensure_fk(
            table_name,
            f"fk_{table_name}_team_id",
            ["team_id"],
            "teams",
            ["id"],
        )
    if include_plan_node:
        _ensure_fk(
            table_name,
            f"fk_{table_name}_plan_node_scope",
            ["tenant_id", "project_id", "organization_id", "plan_node_id"],
            "plan_nodes",
            ["tenant_id", "project_id", "organization_id", "id"],
        )
        _ensure_fk(
            table_name,
            f"fk_{table_name}_goal_scope",
            ["tenant_id", "project_id", "goal_id"],
            "goals",
            ["tenant_id", "project_id", "id"],
        )
        _ensure_fk(
            table_name,
            f"fk_{table_name}_plan_id",
            ["plan_id"],
            "plans",
            ["id"],
        )
    columns = [
        "tenant_id",
        "project_id",
        "organization_id",
        "unit_id",
        "team_id",
        "role_slot_id",
        *(("plan_node_id", "goal_id", "plan_id") if include_plan_node else ()),
    ]
    for column in columns:
        _ensure_index(table_name, f"ix_{table_name}_{column}", [column])


def _backfill_legacy_definition_identities(bind) -> None:
    template_rows = bind.execute(
        sa.text("SELECT id, name, description, prompt_template FROM templates ORDER BY name, id")
    ).mappings()
    used: set[str] = set()
    for row in template_rows:
        key = _unique_key(_stable_key(row["name"], "role_template"), str(row["id"]), used)
        payload = {
            "name": row["name"],
            "description": row["description"],
            "prompt_template": row["prompt_template"],
        }
        bind.execute(
            sa.text(
                "UPDATE templates SET definition_key=:key, definition_version=1, "
                "definition_hash=:definition_hash, prompt_hash=:prompt_hash, "
                "definition_lifecycle='active' WHERE id=:id AND definition_key IS NULL"
            ),
            {
                "key": key,
                "definition_hash": _digest(payload),
                "prompt_hash": hashlib.sha256(str(row["prompt_template"] or "").encode("utf-8")).hexdigest(),
                "id": row["id"],
            },
        )

    blueprint_rows = bind.execute(
        sa.text("SELECT id, name, description, base_team_type_name, is_seed FROM team_blueprints ORDER BY name, id")
    ).mappings()
    used = set()
    table_names = set(sa.inspect(bind).get_table_names())
    for row in blueprint_rows:
        key = _unique_key(_stable_key(row["name"], "team_blueprint"), str(row["id"]), used)
        roles = []
        artifacts = []
        steps = []
        if "blueprint_roles" in table_names:
            roles = [
                dict(item)
                for item in bind.execute(
                    sa.text(
                        "SELECT name, description, sort_order, is_required, config "
                        "FROM blueprint_roles WHERE blueprint_id=:id ORDER BY sort_order, name"
                    ),
                    {"id": row["id"]},
                ).mappings()
            ]
        if "blueprint_artifacts" in table_names:
            artifacts = [
                dict(item)
                for item in bind.execute(
                    sa.text(
                        "SELECT kind, title, description, sort_order, payload "
                        "FROM blueprint_artifacts WHERE blueprint_id=:id ORDER BY sort_order, title"
                    ),
                    {"id": row["id"]},
                ).mappings()
            ]
        if "blueprint_workflow_steps" in table_names:
            steps = [
                dict(item)
                for item in bind.execute(
                    sa.text(
                        "SELECT step_id, role_name, task_kind, title, description, sort_order, "
                        "produces, consumes, depends_on, gate, checks, failure_policy, required_capabilities "
                        "FROM blueprint_workflow_steps WHERE blueprint_id=:id ORDER BY sort_order, step_id"
                    ),
                    {"id": row["id"]},
                ).mappings()
            ]
        payload = {
            "name": row["name"],
            "description": row["description"],
            "base_team_type_name": row["base_team_type_name"],
            "is_seed": bool(row["is_seed"]),
            "roles": roles,
            "artifacts": artifacts,
            "workflow": {
                "mode": "gated",
                "default_failure_policy": "manual",
                "steps": steps,
            }
            if steps
            else None,
        }
        workflow_key = f"{key}_workflow" if steps else None
        bind.execute(
            sa.text(
                "UPDATE team_blueprints SET definition_key=:key, definition_version=1, "
                "definition_hash=:definition_hash, definition_lifecycle='active', "
                "workflow_definition_key=:workflow_key, workflow_definition_version=:workflow_version "
                "WHERE id=:id AND definition_key IS NULL"
            ),
            {
                "key": key,
                "definition_hash": _digest(payload),
                "workflow_key": workflow_key,
                "workflow_version": 1 if steps else None,
                "id": row["id"],
            },
        )


def _sqlite_orphan_preflight(bind) -> None:
    if bind.dialect.name != "sqlite":
        return
    checks = (
        ("goals", ("tenant_id", "project_id"), "projects", ("tenant_id", "project_id"), "project"),
        (
            "goals",
            ("tenant_id", "project_id", "organization_id"),
            "organization_instances",
            ("tenant_id", "project_id", "organization_id"),
            "organization",
        ),
        (
            "goals",
            ("tenant_id", "project_id", "organization_id", "unit_id"),
            "organization_units",
            ("tenant_id", "project_id", "organization_id", "id"),
            "unit",
        ),
        (
            "goals",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "organization_team_links",
            ("tenant_id", "project_id", "organization_id", "team_id"),
            "organization_team",
        ),
        ("goals", ("parent_goal_id",), "goals", ("id",), "parent_goal"),
        ("plans", ("goal_id",), "goals", ("id",), "goal"),
        ("plan_nodes", ("plan_id",), "plans", ("id",), "plan"),
        ("plan_nodes", ("team_id",), "teams", ("id",), "legacy_team"),
        ("archived_tasks", ("team_id",), "teams", ("id",), "legacy_team"),
        *tuple(_runtime_composite_preflight_checks("plan_nodes", include_plan_node=False)),
        *tuple(_runtime_composite_preflight_checks("tasks", include_plan_node=True)),
        *tuple(_runtime_composite_preflight_checks("archived_tasks", include_plan_node=True)),
    )
    for child, child_columns, parent, parent_columns, label in checks:
        complete = " AND ".join(f"c.{column} IS NOT NULL" for column in child_columns)
        join = " AND ".join(
            f"c.{child_column}=p.{parent_column}"
            for child_column, parent_column in zip(child_columns, parent_columns, strict=True)
        )
        count = bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p "
                f"ON {join} WHERE {complete} AND p.{parent_columns[-1]} IS NULL"
            )
        ).scalar_one()
        if count:
            raise RuntimeError(f"organization_fk_orphan_preflight_failed:{child}.{label}:{count}")


def _runtime_composite_preflight_checks(
    table_name: str,
    *,
    include_plan_node: bool,
):
    prefix = ("tenant_id", "project_id")
    organization_prefix = (*prefix, "organization_id")
    yield (table_name, prefix, "projects", prefix, "project")
    yield (
        table_name,
        organization_prefix,
        "organization_instances",
        organization_prefix,
        "organization",
    )
    yield (
        table_name,
        (*organization_prefix, "unit_id"),
        "organization_units",
        (*organization_prefix, "id"),
        "unit",
    )
    yield (
        table_name,
        (*organization_prefix, "team_id"),
        "organization_team_links",
        (*organization_prefix, "team_id"),
        "organization_team",
    )
    yield (
        table_name,
        (*organization_prefix, "role_slot_id"),
        "organization_role_slots",
        (*organization_prefix, "id"),
        "role_slot",
    )
    if include_plan_node:
        yield (
            table_name,
            (*organization_prefix, "plan_node_id"),
            "plan_nodes",
            (*organization_prefix, "id"),
            "plan_node",
        )
        yield (
            table_name,
            (*prefix, "goal_id"),
            "goals",
            (*prefix, "id"),
            "goal",
        )
        yield (table_name, ("plan_id",), "plans", ("id",), "plan")


def _add_columns(table_name: str, columns: list[sa.Column]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    missing = [column for column in columns if column.name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch:
        for column in missing:
            batch.add_column(column)


def _ensure_index(table_name: str, name: str, columns: list[str]) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if name not in existing:
        op.create_index(name, table_name, columns)


def _ensure_unique(table_name: str, name: str, columns: list[str]) -> None:
    existing = {constraint["name"] for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)}
    if name not in existing:
        with op.batch_alter_table(table_name) as batch:
            batch.create_unique_constraint(name, columns)


def _ensure_fk(table_name: str, name: str, columns: list[str], target: str, target_columns: list[str]) -> None:
    existing = {constraint.get("name") for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table_name)}
    if name not in existing:
        with op.batch_alter_table(table_name) as batch:
            batch.create_foreign_key(name, target, columns, target_columns, ondelete="RESTRICT")


def _drop_runtime_binding_columns(bind) -> None:
    specs = RUNTIME_BINDING_COLUMNS
    for table_name, columns in specs.items():
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch:
            for column in reversed(columns):
                if column in existing:
                    batch.drop_column(column)


def _drop_runtime_binding_indexes(bind) -> None:
    table_names = set(sa.inspect(bind).get_table_names())
    for table_name, names in RUNTIME_BINDING_INDEXES.items():
        if table_name not in table_names:
            continue
        existing = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
        for name in names:
            if name in existing:
                op.drop_index(name, table_name=table_name)


def _drop_runtime_binding_constraints(bind) -> None:
    foreign_keys = {
        "goals": (
            "fk_goals_parent_scope",
            "fk_goals_parent_goal_id",
            "fk_goals_organization_team_scope",
            "fk_goals_unit_scope",
            "fk_goals_organization_scope",
            "fk_goals_project_scope",
        ),
        "plans": ("fk_plans_goal_id",),
        "plan_nodes": (
            "fk_plan_nodes_team_id",
            "fk_plan_nodes_role_slot_scope",
            "fk_plan_nodes_organization_team_scope",
            "fk_plan_nodes_unit_scope",
            "fk_plan_nodes_organization_scope",
            "fk_plan_nodes_project_scope",
            "fk_plan_nodes_plan_id",
        ),
        "tasks": (
            "fk_tasks_plan_id",
            "fk_tasks_goal_scope",
            "fk_tasks_plan_node_scope",
            "fk_tasks_role_slot_scope",
            "fk_tasks_organization_team_scope",
            "fk_tasks_unit_scope",
            "fk_tasks_organization_scope",
            "fk_tasks_project_scope",
        ),
        "archived_tasks": (
            "fk_archived_tasks_plan_id",
            "fk_archived_tasks_goal_scope",
            "fk_archived_tasks_plan_node_scope",
            "fk_archived_tasks_team_id",
            "fk_archived_tasks_role_slot_scope",
            "fk_archived_tasks_organization_team_scope",
            "fk_archived_tasks_unit_scope",
            "fk_archived_tasks_organization_scope",
            "fk_archived_tasks_project_scope",
        ),
    }
    uniques = {
        "goals": (
            "uq_goals_organization_scope_id",
            "uq_goals_project_scope_id",
        ),
        "plan_nodes": ("uq_plan_nodes_organization_scope_id",),
        "tasks": ("uq_tasks_organization_scope_id",),
        "archived_tasks": ("uq_archived_tasks_organization_scope_id",),
    }
    for table_name, names in foreign_keys.items():
        if table_name not in set(sa.inspect(bind).get_table_names()):
            continue
        existing = {constraint.get("name") for constraint in sa.inspect(bind).get_foreign_keys(table_name)}
        with op.batch_alter_table(table_name) as batch:
            for name in names:
                if name in existing:
                    batch.drop_constraint(name, type_="foreignkey")
    for table_name, names in uniques.items():
        existing = {constraint.get("name") for constraint in sa.inspect(bind).get_unique_constraints(table_name)}
        with op.batch_alter_table(table_name) as batch:
            for name in names:
                if name in existing:
                    batch.drop_constraint(name, type_="unique")


def _drop_legacy_definition_columns(bind) -> None:
    specs = LEGACY_DEFINITION_COLUMNS
    # Same rule as the runtime bindings: SQLite rebuilds the table to drop a
    # column and recreates its indexes on the new table, so the indexes this
    # migration added have to go first.
    for table_name, names in LEGACY_DEFINITION_INDEXES.items():
        if table_name not in set(sa.inspect(bind).get_table_names()):
            continue
        present = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
        for name in names:
            if name in present:
                op.drop_index(name, table_name=table_name)
    for table_name, columns in specs.items():
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch:
            for column in reversed(columns):
                if column in existing:
                    batch.drop_column(column)


def _stable_key(value: object, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return (normalized or fallback)[:160]


def _unique_key(candidate: str, row_id: str, used: set[str]) -> str:
    key = candidate
    if key in used:
        key = f"{candidate[:150]}_{hashlib.sha256(row_id.encode('utf-8')).hexdigest()[:8]}"
    used.add(key)
    return key


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
