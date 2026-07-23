"""Fail-closed feature flags for the additive Kanban API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


KANBAN_API_ENABLED = "kanban_api"
KANBAN_WRITE_ENABLED = "kanban_write"


def _strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true"}
    return False


@dataclass(frozen=True)
class KanbanFeatureFlags:
    api_enabled: bool
    write_enabled: bool

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None = None) -> "KanbanFeatureFlags":
        config = config or {}
        nested = config.get("KANBAN_FEATURE_FLAGS")
        nested = nested if isinstance(nested, Mapping) else {}
        api_value = config.get(
            "KANBAN_API_ENABLED",
            nested.get(KANBAN_API_ENABLED, os.getenv("ANANTA_KANBAN_API_ENABLED")),
        )
        write_value = config.get(
            "KANBAN_WRITE_ENABLED",
            nested.get(KANBAN_WRITE_ENABLED, os.getenv("ANANTA_KANBAN_WRITE_ENABLED")),
        )
        api_enabled = _strict_bool(api_value)
        return cls(api_enabled, api_enabled and _strict_bool(write_value))

