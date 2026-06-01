"""Add pair_groups and pair_group_members tables

Revision ID: f2e3d4c5b6a7
Revises: d7e8f9a0b1c2, e8f9a0b1c2d3
Create Date: 2026-06-01 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2e3d4c5b6a7'
down_revision: Union[str, Sequence[str], None] = ('d7e8f9a0b1c2', 'e8f9a0b1c2d3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pair_groups',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('owner_user_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False, server_default=''),
        sa.Column('default_permissions', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pair_groups_owner_user_id', 'pair_groups', ['owner_user_id'])
    op.create_index('ix_pair_groups_name', 'pair_groups', ['name'])

    op.create_table(
        'pair_group_members',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('group_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False, server_default=''),
        sa.Column('added_at', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['pair_groups.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_pair_group_member'),
    )
    op.create_index('ix_pair_group_members_group_id', 'pair_group_members', ['group_id'])
    op.create_index('ix_pair_group_members_user_id', 'pair_group_members', ['user_id'])


def downgrade() -> None:
    op.drop_table('pair_group_members')
    op.drop_table('pair_groups')
