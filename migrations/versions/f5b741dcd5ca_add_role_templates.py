"""add role_templates to team

Revision ID: f5b741dcd5ca
Revises: f5b741dcd5c9
Create Date: 2026-01-17 16:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'f5b741dcd5ca'
down_revision: Union[str, Sequence[str], None] = 'f5b741dcd5c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('teams', sa.Column('role_templates', sa.JSON(), nullable=True))

def downgrade() -> None:
    op.drop_column('teams', 'role_templates')
