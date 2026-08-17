"""Tiny schema ledger. Incompatible snapshots become a new file, not in-place mutations."""

from __future__ import annotations

from worker.retrieval.duckdb_schema import SCHEMA_VERSION, apply_schema
from worker.retrieval.vector_store_contract import VectorStoreError


def ensure_current_schema(connection) -> str:
    apply_schema(connection)
    rows = connection.execute(
        "SELECT schema_version FROM snapshot_meta LIMIT 1"
    ).fetchall()
    if not rows:
        return SCHEMA_VERSION
    current = str(rows[0][0] or "")
    if current and current != SCHEMA_VERSION:
        raise VectorStoreError("duckdb_schema_incompatible")
    return current or SCHEMA_VERSION
