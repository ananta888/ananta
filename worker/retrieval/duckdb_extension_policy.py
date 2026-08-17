"""Fail-closed DuckDB extension and SQL surface policy."""

from __future__ import annotations

import re
from typing import Any, Iterable

from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorStoreError

_FORBIDDEN_SQL = re.compile(
    r"\b(ATTACH|DETACH|INSTALL|LOAD|COPY|EXPORT|IMPORT|PRAGMA|CALL|SET\s+GLOBAL|CREATE\s+SECRET)\b",
    re.I,
)


class DuckDBPolicyError(VectorStoreError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)


def apply_runtime_settings(connection: Any, config: DuckDBVectorStoreConfig) -> None:
    statements = [
        "SET enable_external_access=false",
        "SET autoinstall_known_extensions=false",
        "SET autoload_known_extensions=false",
        "SET allow_community_extensions=false",
        "SET allow_unsigned_extensions=false",
        f"SET threads={int(config.resources.threads)}",
        f"SET memory_limit='{config.resources.memory_limit}'",
    ]
    for statement in statements:
        try:
            connection.execute(statement)
        except Exception as exc:  # pragma: no cover - version-dependent settings
            if "enable_external_access" in statement or "autoinstall" in statement or "unsigned" in statement:
                raise DuckDBPolicyError("duckdb_security_setting_rejected") from exc


def assert_safe_sql(sql: str) -> None:
    if _FORBIDDEN_SQL.search(str(sql or "")):
        raise DuckDBPolicyError("duckdb_sql_forbidden")


def assert_allowed_extension(name: str, allowed: Iterable[str]) -> None:
    token = str(name or "").strip().lower()
    if token not in {str(item).strip().lower() for item in allowed}:
        raise DuckDBPolicyError(f"duckdb_extension_not_allowlisted:{token}")
