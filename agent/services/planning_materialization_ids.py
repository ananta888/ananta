"""Deterministic identifiers for Hub-owned planning materialization rows."""

from __future__ import annotations

import hashlib


def stable_planning_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


__all__ = [
    "stable_planning_id",
]
