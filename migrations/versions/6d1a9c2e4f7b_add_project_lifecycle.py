"""add tenant-bound project lifecycle

Revision ID: 6d1a9c2e4f7b
Revises: 5c0f3b8d1e4a
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict

from alembic import op
import sqlalchemy as sa

revision = "6d1a9c2e4f7b"
down_revision = "5c0f3b8d1e4a"
branch_labels = None
depends_on = None


_SOURCE_SCOPE_TABLES = (
    "source_connections",
    "source_control_workspace_registrations",
    "source_control_public_remotes",
    "hub_git_remote_registrations",
    "knowledge_index_execution_bindings",
)


def _source_control_scopes(
    bind: sa.Connection,
) -> dict[tuple[str, str], set[str]]:
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    scopes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for table_name in _SOURCE_SCOPE_TABLES:
        if table_name not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not {"tenant_id", "project_id"}.issubset(columns):
            continue
        tenant_column = sa.column("tenant_id", sa.String(191))
        project_column = sa.column("project_id", sa.String(191))
        selected_columns = [tenant_column, project_column]
        has_owner = "owner_id" in columns
        if has_owner:
            selected_columns.append(sa.column("owner_id", sa.String(191)))
        source_table = sa.table(table_name, *selected_columns)
        statement = sa.select(*source_table.c).distinct()
        for row in bind.execute(statement):
            tenant_id = str(row[0] or "").strip()
            project_id = str(row[1] or "").strip()
            if (
                not tenant_id
                or not project_id
                or len(tenant_id) > 191
                or len(project_id) > 191
            ):
                continue
            scope_owners = scopes[(tenant_id, project_id)]
            if has_owner:
                owner_id = str(row[2] or "").strip()
                if owner_id and len(owner_id) <= 191:
                    scope_owners.add(owner_id)
    return scopes


def _backfill_legacy_projects(bind: sa.Connection) -> None:
    scopes = _source_control_scopes(bind)
    if not scopes:
        return

    metadata = sa.MetaData()
    teams = sa.Table("teams", metadata, autoload_with=bind)
    projects = sa.Table("projects", metadata, autoload_with=bind)
    memberships = sa.Table("project_memberships", metadata, autoload_with=bind)
    existing_team_ids = set(bind.execute(sa.select(teams.c.id)).scalars())
    project_id_counts = Counter(project_id for _, project_id in scopes)
    now = time.time()

    for (tenant_id, project_id), owners in sorted(scopes.items()):
        team_id = None
        if project_id_counts[project_id] == 1:
            if project_id not in existing_team_ids:
                values: dict[str, object] = {
                    "id": project_id,
                    "name": f"Legacy project {project_id}"[:255],
                    "description": None,
                    "is_active": True,
                }
                if "role_templates" in teams.c:
                    values["role_templates"] = {}
                if "blueprint_snapshot" in teams.c:
                    values["blueprint_snapshot"] = {}
                bind.execute(sa.insert(teams).values(**values))
                existing_team_ids.add(project_id)
            team_id = project_id

        created_by = sorted(owners)[0] if owners else "migration:source-control"
        bind.execute(
            sa.insert(projects).values(
                tenant_id=tenant_id,
                project_id=project_id,
                name=f"Legacy project {project_id}"[:255],
                description=None,
                status="active",
                origin="legacy_source_control",
                team_id=team_id,
                created_by_subject_id=created_by,
                lock_version=1,
                created_at_epoch=now,
                updated_at_epoch=now,
                archived_at_epoch=None,
            )
        )
        for owner_id in sorted(owners):
            bind.execute(
                sa.insert(memberships).values(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    subject_id=owner_id,
                    role="maintainer",
                    state="active",
                    lock_version=1,
                    created_at_epoch=now,
                    updated_at_epoch=now,
                )
            )

    project_team_rows = bind.execute(
        sa.select(projects.c.tenant_id, projects.c.project_id, projects.c.team_id).where(
            projects.c.team_id.is_not(None)
        )
    ).all()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    metadata = sa.MetaData()
    goals = sa.Table("goals", metadata, autoload_with=bind) if "goals" in tables else None
    tasks = sa.Table("tasks", metadata, autoload_with=bind) if "tasks" in tables else None

    for tenant_id, project_id, team_id in project_team_rows:
        if goals is not None:
            bind.execute(
                sa.update(goals)
                .where(
                    goals.c.team_id == team_id,
                    goals.c.tenant_id.is_(None),
                    goals.c.project_id.is_(None),
                )
                .values(tenant_id=tenant_id, project_id=project_id)
            )
        if tasks is not None:
            bind.execute(
                sa.update(tasks)
                .where(
                    tasks.c.team_id == team_id,
                    tasks.c.tenant_id.is_(None),
                    tasks.c.project_id.is_(None),
                )
                .values(tenant_id=tenant_id, project_id=project_id)
            )

    if goals is not None and tasks is not None:
        mapped_goals = bind.execute(
            sa.select(goals.c.id, goals.c.tenant_id, goals.c.project_id).where(
                goals.c.tenant_id.is_not(None),
                goals.c.project_id.is_not(None),
            )
        ).all()
        for goal_id, tenant_id, project_id in mapped_goals:
            bind.execute(
                sa.update(tasks)
                .where(
                    tasks.c.goal_id == goal_id,
                    tasks.c.tenant_id.is_(None),
                    tasks.c.project_id.is_(None),
                )
                .values(tenant_id=tenant_id, project_id=project_id)
            )


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("tenant_id", sa.String(191), primary_key=True),
        sa.Column("project_id", sa.String(191), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("origin", sa.String(32), nullable=False, server_default="native"),
        sa.Column("team_id", sa.String(191), nullable=True),
        sa.Column("created_by_subject_id", sa.String(191), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        sa.Column("archived_at_epoch", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_projects_team_id_teams",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("team_id", name="uq_projects_team_id"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
        sa.CheckConstraint(
            "origin IN ('native', 'legacy_source_control')",
            name="ck_projects_origin",
        ),
        sa.CheckConstraint(
            "team_id IS NULL OR team_id = project_id",
            name="ck_projects_backing_team_identity",
        ),
    )
    op.create_index("ix_projects_tenant_status", "projects", ["tenant_id", "status"])

    op.create_table(
        "project_memberships",
        sa.Column("tenant_id", sa.String(191), primary_key=True),
        sa.Column("project_id", sa.String(191), primary_key=True),
        sa.Column("subject_id", sa.String(191), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at_epoch", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.project_id"],
            name="fk_project_memberships_project",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'maintainer', 'viewer')",
            name="ck_project_memberships_role",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'revoked')",
            name="ck_project_memberships_state",
        ),
    )
    op.create_index(
        "ix_project_memberships_subject",
        "project_memberships",
        ["tenant_id", "subject_id", "state"],
    )

    with op.batch_alter_table("goals") as batch_op:
        batch_op.add_column(sa.Column("tenant_id", sa.String(191), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(191), nullable=True))
        batch_op.create_index("ix_goals_tenant_id", ["tenant_id"])
        batch_op.create_index("ix_goals_project_id", ["project_id"])
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("tenant_id", sa.String(191), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(191), nullable=True))
        batch_op.create_index("ix_tasks_tenant_id", ["tenant_id"])
        batch_op.create_index("ix_tasks_project_id", ["project_id"])

    _backfill_legacy_projects(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_project_id")
        batch_op.drop_index("ix_tasks_tenant_id")
        batch_op.drop_column("project_id")
        batch_op.drop_column("tenant_id")
    with op.batch_alter_table("goals") as batch_op:
        batch_op.drop_index("ix_goals_project_id")
        batch_op.drop_index("ix_goals_tenant_id")
        batch_op.drop_column("project_id")
        batch_op.drop_column("tenant_id")
    op.drop_index("ix_project_memberships_subject", table_name="project_memberships")
    op.drop_table("project_memberships")
    op.drop_index("ix_projects_tenant_status", table_name="projects")
    op.drop_table("projects")
