"""add template name uniqueness

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-04-05 15:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name FROM templates ORDER BY name, id")).mappings()
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    for row in rows:
        name = str(row["name"]).strip()
        if name in seen:
            duplicate_ids.append(str(row["id"]))
            continue
        seen.add(name)
        if name != row["name"]:
            bind.execute(sa.text("UPDATE templates SET name = :name WHERE id = :row_id"), {"name": name, "row_id": str(row["id"])})

    for row_id in duplicate_ids:
        bind.execute(sa.text("DELETE FROM templates WHERE id = :row_id"), {"row_id": row_id})

    with op.batch_alter_table("templates", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_templates_name", ["name"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("templates", schema=None) as batch_op:
        batch_op.drop_constraint("uq_templates_name", type_="unique")
