"""add archived_tasks table

Revision ID: 6f9a1b2c3d4e
Revises: 1c2d3e4f5a6b
Create Date: 2026-02-08 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '6f9a1b2c3d4e'
down_revision: Union[str, Sequence[str], None] = '1c2d3e4f5a6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'archived_tasks',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('priority', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.Column('archived_at', sa.Float(), nullable=False),
        sa.Column('team_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('assigned_agent_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('assigned_role_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('history', sa.JSON(), nullable=True),
        sa.Column('last_proposal', sa.JSON(), nullable=True),
        sa.Column('last_output', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('last_exit_code', sa.Integer(), nullable=True),
        sa.Column('callback_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('callback_token', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('parent_task_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('archived_tasks')
