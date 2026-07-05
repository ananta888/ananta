"""Feature flags for the SQLite-backed CodeCompassGraphStore.

Defaults are ``off``: the JSON-backed store remains compatible and
default. SQLite is opt-in per workspace.
"""
from __future__ import annotations

GROUP = "sqlite"


def flags() -> dict[str, bool]:
    return {
        "graph_store_enabled": False,
        "rig_tables_enabled": False,
    }