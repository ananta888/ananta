"""Named, parameterized analytics templates. No free-form SQL."""

from __future__ import annotations

from typing import Any, Mapping

from worker.retrieval.duckdb_extension_policy import assert_safe_sql
from worker.retrieval.vector_store_contract import VectorScope, VectorStoreError

TEMPLATES: dict[str, str] = {
    "document_counts_by_kind": """
        SELECT kind, COUNT(*) AS count
        FROM documents
        WHERE workspace_id = ? AND repository_id = ? AND profile_name = ? AND domain = ?
          AND tombstone = FALSE
        GROUP BY kind
        ORDER BY count DESC, kind
        LIMIT ?
    """,
    "paths_for_kind": """
        SELECT record_id, path, kind, symbol
        FROM documents
        WHERE workspace_id = ? AND repository_id = ? AND profile_name = ? AND domain = ?
          AND tombstone = FALSE AND kind = ?
        ORDER BY path
        LIMIT ?
    """,
    "graph_relation_counts": """
        SELECT relation, COUNT(*) AS count
        FROM graph_edges
        GROUP BY relation
        ORDER BY count DESC, relation
        LIMIT ?
    """,
    "snapshot_identity": """
        SELECT schema_version, workspace_id, repository_id, profile_name, domain,
               manifest_hash, compatibility_fingerprint, source_revision
        FROM snapshot_meta
        LIMIT 1
    """,
}


def run_template(
    connection,
    *,
    name: str,
    scope: VectorScope,
    params: Mapping[str, Any] | None = None,
    max_rows: int = 100,
) -> list[dict[str, Any]]:
    sql = TEMPLATES.get(str(name or ""))
    if sql is None:
        raise VectorStoreError("duckdb_unknown_query_template")
    assert_safe_sql(sql)
    limit = max(1, min(int(max_rows), 1000))
    values: list[Any]
    if name == "document_counts_by_kind":
        values = [scope.workspace_id, scope.repository_id, scope.profile_name, scope.domain, limit]
    elif name == "paths_for_kind":
        kind = str((params or {}).get("kind") or "").strip()
        if not kind:
            raise VectorStoreError("duckdb_template_param_required")
        values = [scope.workspace_id, scope.repository_id, scope.profile_name, scope.domain, kind, limit]
    elif name == "graph_relation_counts":
        values = [limit]
    else:
        values = []
    cursor = connection.execute(sql, values)
    columns = [item[0] for item in cursor.description]
    rows = []
    for raw in cursor.fetchmany(limit):
        rows.append({columns[index]: raw[index] for index in range(len(columns))})
    return rows
