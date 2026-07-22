"""Merge SFU security and speech mutation migration branches.

Revision ID: b35ae1f2c4d6
Revises: 4f9c2a7e1b6d, a249d0e1f2a3
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "b35ae1f2c4d6"
down_revision: str | Sequence[str] | None = (
    "4f9c2a7e1b6d",
    "a249d0e1f2a3",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join two independently applied additive branches without schema DDL."""


def downgrade() -> None:
    """Remove only the merge marker; parent migrations own their schema DDL."""
