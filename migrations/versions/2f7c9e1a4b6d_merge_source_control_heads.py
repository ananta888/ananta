"""Merge source-control migration heads.

Revision ID: 2f7c9e1a4b6d
Revises: 1e6a8c0d2f4b, 1e6f8a0c2d4b
"""

from collections.abc import Sequence


revision: str = "2f7c9e1a4b6d"
down_revision: str | Sequence[str] | None = (
    "1e6a8c0d2f4b",
    "1e6f8a0c2d4b",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge both branches without changing the schema."""


def downgrade() -> None:
    """Split the merge point without changing the schema."""
