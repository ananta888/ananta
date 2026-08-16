"""Add normalized organization planning control-plane persistence.

Revision ID: 4f9d1a3b5c7e
Revises: 3e8c0f2a4b6d
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.enterprise_organization_schema_v1 import enterprise_organization_tables_v1

revision = "4f9d1a3b5c7e"
down_revision = "3e8c0f2a4b6d"
branch_labels = None
depends_on = None


PLANNING_TABLE_ORDER = (
    "planning_artifact_revisions",
    "planning_lineage",
    "planning_operation_receipts",
    "planning_task_mappings",
    "planning_amendment_inputs",
    "planning_task_dispatches",
    "worker_task_proposals",
)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_approval_requests_table(bind)
    _extend_approval_requests(bind)
    _sqlite_approval_scope_preflight(bind)
    _create_approval_scope_constraints(bind)
    # Parent rows are created before every child that references them:
    # revisions -> receipts -> mappings -> dispatches -> proposals.
    _create_planning_tables(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _planning_tables()
    existing = set(sa.inspect(bind).get_table_names())
    for name in reversed(PLANNING_TABLE_ORDER):
        if name in existing:
            tables[name].drop(bind=bind, checkfirst=True)
    # Planning artifacts reference approval_requests; drop the children before
    # SQLite batch-recreates the legacy parent table.
    _drop_approval_request_extensions(bind)


def _planning_tables() -> dict[str, sa.Table]:
    tables = enterprise_organization_tables_v1()
    return {name: tables[name] for name in PLANNING_TABLE_ORDER}


def _ensure_approval_requests_table(bind) -> None:
    if "approval_requests" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(191), nullable=False, primary_key=True),
    )


def _create_planning_tables(bind) -> None:
    existing = set(sa.inspect(bind).get_table_names())
    for name, table in _planning_tables().items():
        if name not in existing:
            table.create(bind=bind, checkfirst=True)
            existing.add(name)


def _extend_approval_requests(bind) -> None:
    if "approval_requests" not in set(sa.inspect(bind).get_table_names()):
        return
    _add_columns(
        "approval_requests",
        [
            sa.Column("tenant_id", sa.String(191), nullable=True),
            sa.Column("project_id", sa.String(191), nullable=True),
            sa.Column("organization_id", sa.String(191), nullable=True),
            sa.Column("approval_intent_key", sa.String(64), nullable=True),
        ],
    )
    for column in ("tenant_id", "project_id", "organization_id"):
        _ensure_index(
            "approval_requests",
            f"ix_approval_requests_{column}",
            [column],
        )
    _ensure_index(
        "approval_requests",
        "ix_approval_requests_approval_intent_key",
        ["approval_intent_key"],
        unique=True,
    )


def _create_approval_scope_constraints(bind) -> None:
    if "approval_requests" not in set(sa.inspect(bind).get_table_names()):
        return
    _ensure_fk(
        "approval_requests",
        "fk_approval_requests_project_scope",
        ["tenant_id", "project_id"],
        "projects",
        ["tenant_id", "project_id"],
    )
    _ensure_fk(
        "approval_requests",
        "fk_approval_requests_organization_scope",
        ["tenant_id", "project_id", "organization_id"],
        "organization_instances",
        ["tenant_id", "project_id", "organization_id"],
    )


def _sqlite_approval_scope_preflight(bind) -> None:
    if bind.dialect.name != "sqlite" or "approval_requests" not in set(sa.inspect(bind).get_table_names()):
        return
    checks = (
        (
            "project",
            "projects",
            ("tenant_id", "project_id"),
        ),
        (
            "organization",
            "organization_instances",
            ("tenant_id", "project_id", "organization_id"),
        ),
    )
    for label, parent, columns in checks:
        complete = " AND ".join(f"c.{column} IS NOT NULL" for column in columns)
        join = " AND ".join(f"c.{column}=p.{column}" for column in columns)
        missing = bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM approval_requests c LEFT JOIN {parent} p "
                f"ON {join} WHERE {complete} AND p.{columns[-1]} IS NULL"
            )
        ).scalar_one()
        if missing:
            raise RuntimeError(f"planning_fk_orphan_preflight_failed:approval_requests.{label}:{missing}")


def _add_columns(table_name: str, columns: list[sa.Column]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    with op.batch_alter_table(table_name) as batch:
        for column in columns:
            if column.name not in existing:
                batch.add_column(column)


def _ensure_index(
    table_name: str,
    name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if name not in existing:
        op.create_index(name, table_name, columns, unique=unique)


def _ensure_fk(
    table_name: str,
    name: str,
    columns: list[str],
    target: str,
    target_columns: list[str],
) -> None:
    existing = {constraint.get("name") for constraint in sa.inspect(op.get_bind()).get_foreign_keys(table_name)}
    if name not in existing:
        with op.batch_alter_table(table_name) as batch:
            batch.create_foreign_key(
                name,
                target,
                columns,
                target_columns,
                ondelete="RESTRICT",
            )


def _drop_approval_request_extensions(bind) -> None:
    if "approval_requests" not in set(sa.inspect(bind).get_table_names()):
        return
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("approval_requests")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("approval_requests")}
    existing_foreign_keys = {constraint.get("name") for constraint in inspector.get_foreign_keys("approval_requests")}
    with op.batch_alter_table("approval_requests") as batch:
        for name in (
            "fk_approval_requests_organization_scope",
            "fk_approval_requests_project_scope",
        ):
            if name in existing_foreign_keys:
                batch.drop_constraint(name, type_="foreignkey")
    for name in (
        "ix_approval_requests_approval_intent_key",
        "ix_approval_requests_organization_id",
        "ix_approval_requests_project_id",
        "ix_approval_requests_tenant_id",
    ):
        if name in existing_indexes:
            op.drop_index(name, table_name="approval_requests")
    with op.batch_alter_table("approval_requests") as batch:
        for column in (
            "approval_intent_key",
            "organization_id",
            "project_id",
            "tenant_id",
        ):
            if column in existing_columns:
                batch.drop_column(column)
