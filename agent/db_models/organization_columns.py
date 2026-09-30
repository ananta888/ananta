"""Column helpers shared by the normalized organization persistence models."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def json_column(default: Any) -> sa.Column:
    return sa.Column(sa.JSON(), nullable=False, default=default)


__all__ = ["json_column"]
