"""add team blueprint persistence

Revision ID: 2a4b6c8d0e1f
Revises: 1c2d3e4f5a6b
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a4b6c8d0e1f"
down_revision: Union[str, Sequence[str], None] = "1c2d3e4f5a6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "team_blueprints",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("base_team_type_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("team_blueprints", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_team_blueprints_name"), ["name"], unique=True)

    op.create_table(
        "blueprint_roles",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blueprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("template_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["blueprint_id"], ["team_blueprints.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("blueprint_roles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_blueprint_roles_blueprint_id"), ["blueprint_id"], unique=False)

    op.create_table(
        "blueprint_artifacts",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blueprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["blueprint_id"], ["team_blueprints.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("blueprint_artifacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_blueprint_artifacts_blueprint_id"), ["blueprint_id"], unique=False)

    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("blueprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("blueprint_snapshot", sa.JSON(), nullable=True))
        batch_op.create_foreign_key("fk_teams_blueprint_id_team_blueprints", "team_blueprints", ["blueprint_id"], ["id"])

    with op.batch_alter_table("team_members", schema=None) as batch_op:
        batch_op.add_column(sa.Column("blueprint_role_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_foreign_key(
            "fk_team_members_blueprint_role_id_blueprint_roles", "blueprint_roles", ["blueprint_role_id"], ["id"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("team_members", schema=None) as batch_op:
        batch_op.drop_constraint("fk_team_members_blueprint_role_id_blueprint_roles", type_="foreignkey")
        batch_op.drop_column("blueprint_role_id")

    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.drop_constraint("fk_teams_blueprint_id_team_blueprints", type_="foreignkey")
        batch_op.drop_column("blueprint_snapshot")
        batch_op.drop_column("blueprint_id")

    with op.batch_alter_table("blueprint_artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_blueprint_artifacts_blueprint_id"))
    op.drop_table("blueprint_artifacts")

    with op.batch_alter_table("blueprint_roles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_blueprint_roles_blueprint_id"))
    op.drop_table("blueprint_roles")

    with op.batch_alter_table("team_blueprints", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_team_blueprints_name"))
    op.drop_table("team_blueprints")
