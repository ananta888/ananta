"""backfill canonical task statuses

Revision ID: 8c1d2e3f4a5b
Revises: 7b3c4d5e6f7a
Create Date: 2026-02-16 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8c1d2e3f4a5b"
down_revision: Union[str, Sequence[str], None] = "7b3c4d5e6f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_statuses(table: str) -> None:
    bind = op.get_bind()
    status_backfill = {
        "done": "completed",
        "complete": "completed",
        "in-progress": "in_progress",
        "in progress": "in_progress",
        "to-do": "todo",
        "backlog": "todo",
    }
    for old_status, canonical_status in status_backfill.items():
        bind.execute(
            sa.text(f"UPDATE {table} SET status = :new_status WHERE lower(trim(status)) = :old_status"),
            {"new_status": canonical_status, "old_status": old_status},
        )


def upgrade() -> None:
    _backfill_statuses("tasks")
    _backfill_statuses("archived_tasks")


def downgrade() -> None:
    # Data backfill is intentionally not reverted.
    pass

