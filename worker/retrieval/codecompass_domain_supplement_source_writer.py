"""Private SQLite source store for complete semantic adapter output."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)
from worker.retrieval.codecompass_domain_supplement_config import (
    configured_domain_supplement_source_bytes,
    validate_domain_supplement_source_bytes,
)
from worker.retrieval.codecompass_domain_supplement_primitives import (
    _SOURCE_SCHEMA,
    _SOURCE_SQLITE_APPLICATION_ID,
    _SOURCE_SQLITE_USER_VERSION,
    _SQLITE_PAGE_BYTES,
    CodeCompassDomainSupplementExecutionDeadlinePort,
    SemanticDomainIdentity,
    _canonical_json_bytes,
    _canonical_json_text,
    _checkpoint,
    _sqlite_deadline_progress,
    _valid_prefixed_sha256,
)


class CodeCompassDomainSupplementSourceWriter:
    """Persist complete semantic adapter rows without holding the repo in RAM."""

    _FATAL_DIAGNOSTIC_CODES = frozenset(
        {
            "parser_failed",
            "parser_limit_exceeded",
            "parser_timeout",
            "security_blocked",
            "python_parse_error",
            "python_syntax_error",
            "java_parse_error",
        }
    )

    def __init__(
        self,
        path: str | Path,
        *,
        maximum_bytes: int | None = None,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> None:
        configured_maximum = (
            configured_domain_supplement_source_bytes()
            if maximum_bytes is None
            else validate_domain_supplement_source_bytes(maximum_bytes)
        )
        self._path = Path(path)
        self._maximum_bytes = int(configured_maximum)
        self._execution_deadline = execution_deadline
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            delete=False,
        )
        handle.close()
        self._temporary_path = Path(handle.name)
        self._closed = False
        self._connection: sqlite3.Connection | None = None
        try:
            _checkpoint(self._execution_deadline)
            self._connection = sqlite3.connect(str(self._temporary_path))
            self._initialize(
                self._connection,
                maximum_bytes=self._maximum_bytes,
                execution_deadline=self._execution_deadline,
            )
        except Exception:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            self._temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _initialize(
        connection: sqlite3.Connection,
        *,
        maximum_bytes: int,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ),
    ) -> None:
        maximum_pages = max(1, int(maximum_bytes) // _SQLITE_PAGE_BYTES)
        try:
            with _sqlite_deadline_progress(connection, execution_deadline):
                connection.execute(f"PRAGMA page_size={_SQLITE_PAGE_BYTES}")
                connection.execute("PRAGMA auto_vacuum=NONE")
                connection.execute("PRAGMA journal_mode=OFF")
                connection.execute("PRAGMA synchronous=OFF")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    f"PRAGMA application_id={_SOURCE_SQLITE_APPLICATION_ID}"
                )
                connection.execute(
                    f"PRAGMA user_version={_SOURCE_SQLITE_USER_VERSION}"
                )
                connection.execute(f"PRAGMA max_page_count={maximum_pages}")
                connection.executescript(
                    """
            CREATE TABLE source_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE domains (
              domain_key TEXT PRIMARY KEY,
              domain_kind TEXT NOT NULL,
              domain_label TEXT NOT NULL,
              source_file_count INTEGER NOT NULL CHECK(source_file_count >= 1)
            ) WITHOUT ROWID;
            CREATE TABLE semantic_nodes (
              domain_key TEXT NOT NULL REFERENCES domains(domain_key),
              node_id TEXT NOT NULL UNIQUE,
              record_json TEXT NOT NULL,
              PRIMARY KEY(domain_key, node_id)
            ) WITHOUT ROWID;
            CREATE TABLE semantic_edges (
              domain_key TEXT NOT NULL REFERENCES domains(domain_key),
              record_sha256 TEXT NOT NULL,
              record_json TEXT NOT NULL,
              PRIMARY KEY(domain_key, record_sha256)
            ) WITHOUT ROWID;
            CREATE TABLE declaration_edges (
              domain_key TEXT NOT NULL REFERENCES domains(domain_key),
              record_sha256 TEXT NOT NULL,
              record_json TEXT NOT NULL,
              PRIMARY KEY(domain_key, record_sha256)
            ) WITHOUT ROWID;
            CREATE TABLE incomplete_domains (
              domain_key TEXT NOT NULL REFERENCES domains(domain_key),
              reason_code TEXT NOT NULL,
              PRIMARY KEY(domain_key, reason_code)
            ) WITHOUT ROWID;
            """
                )
                connection.execute(
                    "INSERT INTO source_meta(key, value) VALUES ('schema', ?)",
                    (_SOURCE_SCHEMA,),
                )
        except sqlite3.OperationalError as exc:
            if CodeCompassDomainSupplementSourceWriter._is_sqlite_full(exc):
                raise RuntimeError(
                    "codecompass_domain_supplement_source_too_large"
                ) from exc
            raise

    def add_domain(
        self,
        identity: SemanticDomainIdentity,
        *,
        source_file_count: int,
    ) -> None:
        connection = self._open_connection()
        _checkpoint(self._execution_deadline)
        if source_file_count < 1:
            raise ValueError("codecompass_domain_supplement_source_file_count_invalid")
        try:
            with _sqlite_deadline_progress(connection, self._execution_deadline):
                existing = connection.execute(
                    "SELECT domain_kind, domain_label, source_file_count "
                    "FROM domains WHERE domain_key = ?",
                    (identity.domain_key,),
                ).fetchone()
                expected = (
                    identity.domain_kind,
                    identity.domain_label,
                    int(source_file_count),
                )
                if existing is not None:
                    if tuple(existing) != expected:
                        raise ValueError(
                            "codecompass_domain_supplement_domain_identity_conflict"
                        )
                    return
                connection.execute(
                    "INSERT INTO domains(domain_key, domain_kind, domain_label, "
                    "source_file_count) VALUES (?, ?, ?, ?)",
                    (identity.domain_key, *expected),
                )
        except sqlite3.OperationalError as exc:
            self._raise_source_operational_error(exc)

    def add_node(self, *, domain_key: str, record: Mapping[str, Any]) -> None:
        connection = self._open_connection()
        _checkpoint(self._execution_deadline)
        normalized = self._record(
            domain_key=domain_key,
            record=record,
            kind="node",
        )
        node_id = str(normalized.get("id") or normalized.get("node_id") or "").strip()
        if not node_id:
            raise ValueError("codecompass_domain_supplement_node_invalid")
        serialized = _canonical_json_text(normalized)
        try:
            with _sqlite_deadline_progress(connection, self._execution_deadline):
                existing = connection.execute(
                    "SELECT domain_key, record_json FROM semantic_nodes "
                    "WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != (domain_key, serialized):
                        raise ValueError(
                            "codecompass_domain_supplement_node_identity_conflict"
                        )
                    return
                connection.execute(
                    "INSERT INTO semantic_nodes(domain_key, node_id, record_json) "
                    "VALUES (?, ?, ?)",
                    (domain_key, node_id, serialized),
                )
        except sqlite3.OperationalError as exc:
            self._raise_source_operational_error(exc)

    def add_semantic_edge(
        self,
        *,
        domain_key: str,
        record: Mapping[str, Any],
    ) -> None:
        self._open_connection()
        _checkpoint(self._execution_deadline)
        normalized = self._record(
            domain_key=domain_key,
            record=record,
            kind="semantic_edge",
        )
        try:
            self._insert_edge("semantic_edges", domain_key, normalized)
        except sqlite3.OperationalError as exc:
            self._raise_source_operational_error(exc)

    def add_declaration_edge(
        self,
        *,
        domain_key: str,
        record: Mapping[str, Any],
    ) -> None:
        self._open_connection()
        _checkpoint(self._execution_deadline)
        normalized = self._record(
            domain_key=domain_key,
            record=record,
            kind="declaration_edge",
        )
        try:
            self._insert_edge("declaration_edges", domain_key, normalized)
        except sqlite3.OperationalError as exc:
            self._raise_source_operational_error(exc)

    def observe_diagnostics(
        self,
        *,
        domain_key: str,
        diagnostics: object,
        emitted_record_count: int,
    ) -> None:
        connection = self._open_connection()
        _checkpoint(self._execution_deadline)
        raw_items = diagnostics if isinstance(diagnostics, (list, tuple)) else ()
        codes = {
            str(item.get("code") or "").strip()
            for item in raw_items
            if isinstance(item, Mapping) and str(item.get("code") or "").strip()
        }
        fatal = codes.intersection(self._FATAL_DIAGNOSTIC_CODES)
        # Registry failures and parser-guard exclusions are empty by contract.
        # A non-empty adapter result with an ordinary diagnostic (for example a
        # dynamic import) remains complete graph evidence.
        if emitted_record_count == 0:
            fatal.update(
                code
                for code in codes
                if code != "semantic_adapter_unsupported"
                and (
                    code.endswith("_parse_error")
                    or code.endswith("_syntax_error")
                )
            )
        try:
            with _sqlite_deadline_progress(connection, self._execution_deadline):
                for reason_code in sorted(fatal):
                    connection.execute(
                        "INSERT OR IGNORE INTO incomplete_domains(domain_key, "
                        "reason_code) VALUES (?, ?)",
                        (domain_key, reason_code),
                    )
        except sqlite3.OperationalError as exc:
            self._raise_source_operational_error(exc)

    def finalize(self) -> Path:
        connection = self._open_connection()
        _checkpoint(self._execution_deadline)
        incomplete = connection.execute(
            "SELECT reason_code FROM incomplete_domains "
            "ORDER BY domain_key, reason_code LIMIT 1"
        ).fetchone()
        if incomplete is not None:
            self.abort()
            raise RuntimeError(
                "codecompass_domain_supplement_incomplete:" + str(incomplete[0])
            )
        try:
            with _sqlite_deadline_progress(connection, self._execution_deadline):
                connection.commit()
            connection.close()
            self._closed = True
            size_bytes = self._temporary_path.stat().st_size
            if size_bytes <= 0 or size_bytes > self._maximum_bytes:
                raise RuntimeError(
                    "codecompass_domain_supplement_source_too_large"
                )
            _checkpoint(self._execution_deadline)
            os.replace(self._temporary_path, self._path)
            return self._path
        except sqlite3.OperationalError as exc:
            self.abort()
            if self._is_sqlite_full(exc):
                raise RuntimeError(
                    "codecompass_domain_supplement_source_too_large"
                ) from exc
            raise
        except Exception:
            self.abort()
            raise

    def abort(self) -> None:
        if not self._closed:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
        if self._temporary_path.exists():
            self._temporary_path.unlink()

    def __enter__(self) -> CodeCompassDomainSupplementSourceWriter:
        return self

    def __exit__(self, *_args: object) -> None:
        if not self._closed:
            self.abort()

    @staticmethod
    def _record(
        *,
        domain_key: str,
        record: Mapping[str, Any],
        kind: str,
    ) -> dict[str, Any]:
        if not _valid_prefixed_sha256(domain_key):
            raise ValueError("codecompass_domain_supplement_domain_key_invalid")
        normalized = dict(record)
        marker = normalized.get(CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD)
        if marker not in (None, domain_key):
            raise ValueError("codecompass_domain_supplement_domain_marker_mismatch")
        normalized[CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD] = domain_key
        encoded = _canonical_json_bytes(normalized) + b"\n"
        if len(encoded) > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES:
            raise RuntimeError(
                f"codecompass_domain_supplement_{kind}_record_too_large"
            )
        return normalized

    def _insert_edge(
        self,
        table: str,
        domain_key: str,
        record: Mapping[str, Any],
    ) -> None:
        connection = self._open_connection()
        source = str(record.get("source") or record.get("source_id") or "").strip()
        target = str(record.get("target") or record.get("target_id") or "").strip()
        if not source or not target:
            raise ValueError("codecompass_domain_supplement_edge_invalid")
        serialized = _canonical_json_text(record)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with _sqlite_deadline_progress(connection, self._execution_deadline):
            connection.execute(
                f"INSERT OR IGNORE INTO {table}(domain_key, record_sha256, "
                "record_json) VALUES (?, ?, ?)",
                (domain_key, digest, serialized),
            )

    def _open_connection(self) -> sqlite3.Connection:
        if self._closed or self._connection is None:
            raise RuntimeError("codecompass_domain_supplement_source_closed")
        return self._connection

    def _raise_source_operational_error(
        self,
        exc: sqlite3.OperationalError,
    ) -> None:
        if not self._is_sqlite_full(exc):
            raise exc
        self.abort()
        raise RuntimeError(
            "codecompass_domain_supplement_source_too_large"
        ) from exc

    @staticmethod
    def _is_sqlite_full(exc: sqlite3.OperationalError) -> bool:
        return (
            getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
            or "full" in str(exc).lower()
        )


__all__ = ["CodeCompassDomainSupplementSourceWriter"]
