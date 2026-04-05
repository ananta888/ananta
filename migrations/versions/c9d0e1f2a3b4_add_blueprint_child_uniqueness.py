"""add blueprint child uniqueness constraints

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-05 12:30:50.000000

"""

from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _delete_duplicate_text_rows(bind, table_name: str, text_column: str) -> None:
    rows = bind.execute(
        sa.text(f"SELECT id, blueprint_id, {text_column} FROM {table_name} ORDER BY blueprint_id, {text_column}, id")
    ).mappings()
    seen: set[tuple[str, str]] = set()
    duplicate_ids: list[str] = []
    for row in rows:
        key = (str(row["blueprint_id"]), str(row[text_column]))
        if key in seen:
            duplicate_ids.append(str(row["id"]))
            continue
        seen.add(key)
    for row_id in duplicate_ids:
        bind.execute(sa.text(f"DELETE FROM {table_name} WHERE id = :row_id"), {"row_id": row_id})


def _normalize_duplicate_sort_orders(bind, table_name: str) -> None:
    rows = bind.execute(
        sa.text(f"SELECT id, blueprint_id, sort_order FROM {table_name} ORDER BY blueprint_id, sort_order, id")
    ).mappings()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["blueprint_id"])].append(dict(row))
    for blueprint_id, entries in grouped.items():
        used: set[int] = set()
        next_candidate = 0
        for row in entries:
            sort_order = int(row["sort_order"])
            if sort_order not in used:
                used.add(sort_order)
                next_candidate = max(next_candidate, sort_order)
                continue
            candidate = max(next_candidate, sort_order)
            while candidate in used:
                candidate += 10
            bind.execute(
                sa.text(f"UPDATE {table_name} SET sort_order = :sort_order WHERE id = :row_id"),
                {"sort_order": candidate, "row_id": str(row["id"])},
            )
            used.add(candidate)
            next_candidate = candidate


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    _delete_duplicate_text_rows(bind, "blueprint_roles", "name")
    _delete_duplicate_text_rows(bind, "blueprint_artifacts", "title")
    _normalize_duplicate_sort_orders(bind, "blueprint_roles")
    _normalize_duplicate_sort_orders(bind, "blueprint_artifacts")

    with op.batch_alter_table("blueprint_roles", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_blueprint_roles_blueprint_name", ["blueprint_id", "name"])
        batch_op.create_unique_constraint("uq_blueprint_roles_blueprint_sort_order", ["blueprint_id", "sort_order"])

    with op.batch_alter_table("blueprint_artifacts", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_blueprint_artifacts_blueprint_title", ["blueprint_id", "title"])
        batch_op.create_unique_constraint("uq_blueprint_artifacts_blueprint_sort_order", ["blueprint_id", "sort_order"])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("blueprint_artifacts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_blueprint_artifacts_blueprint_sort_order", type_="unique")
        batch_op.drop_constraint("uq_blueprint_artifacts_blueprint_title", type_="unique")

    with op.batch_alter_table("blueprint_roles", schema=None) as batch_op:
        batch_op.drop_constraint("uq_blueprint_roles_blueprint_sort_order", type_="unique")
        batch_op.drop_constraint("uq_blueprint_roles_blueprint_name", type_="unique")
