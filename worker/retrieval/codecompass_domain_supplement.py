"""Worker-owned, domain-lazy CodeCompass semantic supplement artifacts.

The repository bridge writes complete adapter output into a private SQLite
source store.  The graph materializer later turns that source into one
revision-bound, deterministic SQLite artifact whose payload is independently
addressable by top-level domain.  The Hub remains the control plane: this
module neither schedules work nor reaches across the Worker boundary.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import urllib.parse
import zlib
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_PAYLOAD_KINDS,
    DOMAIN_SUPPLEMENT_SCHEMA,
    DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID,
    DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION,
    codecompass_domain_supplement_canonical_json_bytes,
    codecompass_domain_supplement_decode_metadata,
    codecompass_domain_supplement_encode_metadata,
    codecompass_domain_supplement_logical_chunk_header,
    codecompass_domain_supplement_logical_domain,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)
from worker.retrieval.codecompass_domain_supplement_config import (
    DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES,
    configured_domain_supplement_source_bytes,
    validate_domain_supplement_source_bytes,
)

DOMAIN_SUPPLEMENT_SOURCE_FILENAME: Final = "semantic_domain_source.sqlite3"
_SOURCE_SCHEMA = "codecompass_graph_domain_supplement_source.v1"
_SOURCE_SQLITE_APPLICATION_ID = 0x414E4353  # ANCS
_SOURCE_SQLITE_USER_VERSION = 1
_DOMAIN_KINDS = frozenset({"top_level_path", "repository_root"})
_SQLITE_HEADER = b"SQLite format 3\x00"
_SQLITE_PAGE_BYTES = 4096
_SQLITE_PROGRESS_OPCODES = 10_000

_SOURCE_TABLE_COLUMNS = {
    "source_meta": (
        ("key", "TEXT", 1, 1),
        ("value", "TEXT", 1, 0),
    ),
    "domains": (
        ("domain_key", "TEXT", 1, 1),
        ("domain_kind", "TEXT", 1, 0),
        ("domain_label", "TEXT", 1, 0),
        ("source_file_count", "INTEGER", 1, 0),
    ),
    "semantic_nodes": (
        ("domain_key", "TEXT", 1, 1),
        ("node_id", "TEXT", 1, 2),
        ("record_json", "TEXT", 1, 0),
    ),
    "semantic_edges": (
        ("domain_key", "TEXT", 1, 1),
        ("record_sha256", "TEXT", 1, 2),
        ("record_json", "TEXT", 1, 0),
    ),
    "declaration_edges": (
        ("domain_key", "TEXT", 1, 1),
        ("record_sha256", "TEXT", 1, 2),
        ("record_json", "TEXT", 1, 0),
    ),
    "incomplete_domains": (
        ("domain_key", "TEXT", 1, 1),
        ("reason_code", "TEXT", 1, 2),
    ),
}


class CodeCompassDomainSupplementExecutionDeadlinePort(Protocol):
    """Narrow cancellation seam for bounded Worker materialization."""

    def checkpoint(self) -> None: ...


def _checkpoint(
    execution_deadline: CodeCompassDomainSupplementExecutionDeadlinePort | None,
) -> None:
    if execution_deadline is not None:
        execution_deadline.checkpoint()


@contextmanager
def _sqlite_deadline_progress(
    connection: sqlite3.Connection,
    execution_deadline: CodeCompassDomainSupplementExecutionDeadlinePort | None,
) -> Iterator[None]:
    """Interrupt long SQLite bytecode loops when the delegated lease expires."""

    if execution_deadline is None:
        yield
        return

    failure: list[Exception] = []

    def _progress() -> int:
        try:
            execution_deadline.checkpoint()
        except Exception as exc:  # SQLite callbacks cannot propagate exceptions.
            if not failure:
                failure.append(exc)
            return 1
        return 0

    connection.set_progress_handler(_progress, _SQLITE_PROGRESS_OPCODES)
    try:
        _checkpoint(execution_deadline)
        try:
            yield
        except sqlite3.OperationalError as exc:
            if failure:
                raise failure[0] from exc
            raise
        if failure:
            raise failure[0]
        _checkpoint(execution_deadline)
    finally:
        connection.set_progress_handler(None, 0)


def _canonical_json_bytes(value: object) -> bytes:
    return codecompass_domain_supplement_canonical_json_bytes(value)


def _canonical_json_text(value: object) -> str:
    return codecompass_domain_supplement_encode_metadata(value)


def _prefixed_sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _valid_prefixed_sha256(value: object) -> bool:
    normalized = str(value or "")
    return (
        len(normalized) == 71
        and normalized.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in normalized[7:])
    )


def _valid_sha256(value: object) -> bool:
    normalized = str(value or "")
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _valid_source_revision_id(value: object) -> bool:
    normalized = str(value or "")
    return (
        len(normalized) == 69
        and normalized.startswith("srev_")
        and _valid_sha256(normalized[5:])
    )


def _read_only_uri(path: Path) -> str:
    return "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro&immutable=1"


def _bounded_decompress(payload: bytes, *, expected_size: int) -> bytes:
    if expected_size < 0 or expected_size > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES:
        raise ValueError("codecompass_domain_supplement_chunk_size_invalid")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload, expected_size + 1)
    if (
        len(raw) != expected_size
        or decoder.unconsumed_tail
        or decoder.unused_data
        or not decoder.eof
    ):
        raise ValueError("codecompass_domain_supplement_chunk_invalid")
    return raw


@dataclass(frozen=True)
class SemanticDomainIdentity:
    domain_key: str
    domain_kind: str
    domain_label: str

    def __post_init__(self) -> None:
        if not _valid_prefixed_sha256(self.domain_key):
            raise ValueError("codecompass_domain_supplement_domain_key_invalid")
        if self.domain_kind not in _DOMAIN_KINDS:
            raise ValueError("codecompass_domain_supplement_domain_kind_invalid")
        if self.domain_kind == "repository_root" and self.domain_label:
            raise ValueError("codecompass_domain_supplement_root_label_invalid")
        if self.domain_kind == "top_level_path" and not self.domain_label:
            raise ValueError("codecompass_domain_supplement_domain_label_invalid")


@dataclass(frozen=True)
class DomainSupplementContent:
    logical_content_hash: str
    domain_count: int
    semantic_node_count: int
    semantic_edge_count: int
    declaration_edge_count: int


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


class WorkerCodeCompassDomainSupplementMaterializer:
    """Compress and bind a complete raw semantic source to one graph revision."""

    def __init__(
        self,
        *,
        maximum_bytes: int = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    ) -> None:
        if maximum_bytes <= 0 or maximum_bytes > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES:
            raise ValueError("codecompass_domain_supplement_limit_invalid")
        self._maximum_bytes = int(maximum_bytes)

    def inspect_source(
        self,
        source_path: str | Path,
        *,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> DomainSupplementContent:
        _checkpoint(execution_deadline)
        path = self._validated_source_path(source_path)
        with closing(
            self._connect_source(
                path,
                execution_deadline=execution_deadline,
            )
        ) as connection:
            with _sqlite_deadline_progress(connection, execution_deadline):
                return self._logical_content(
                    connection,
                    execution_deadline=execution_deadline,
                )

    def materialize(
        self,
        *,
        source_path: str | Path,
        output_path: str | Path,
        graph_revision: str,
        source_scope: str,
        knowledge_index_id: str,
        source_id: str,
        source_revision_id: str,
        source_revision_digest: str,
        expected_content_hash: str | None = None,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> dict[str, Any]:
        _checkpoint(execution_deadline)
        if not _valid_prefixed_sha256(graph_revision):
            raise ValueError("codecompass_domain_supplement_graph_revision_invalid")
        if not source_scope or not knowledge_index_id:
            raise ValueError("codecompass_domain_supplement_binding_invalid")
        if (
            not _valid_source_revision_id(source_revision_id)
            or not _valid_sha256(source_revision_digest)
            or source_id != f"bound-source:{source_revision_id}"
        ):
            raise ValueError("codecompass_domain_supplement_source_revision_invalid")

        source = self._validated_source_path(source_path)
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        )
        handle.close()
        temporary = Path(handle.name)
        try:
            with closing(
                self._connect_source(
                    source,
                    execution_deadline=execution_deadline,
                )
            ) as source_connection:
                with _sqlite_deadline_progress(
                    source_connection,
                    execution_deadline,
                ):
                    content = self._logical_content(
                        source_connection,
                        execution_deadline=execution_deadline,
                    )
                if (
                    expected_content_hash is not None
                    and content.logical_content_hash != expected_content_hash
                ):
                    raise RuntimeError(
                        "codecompass_domain_supplement_source_content_changed"
                    )
                target = sqlite3.connect(str(temporary))
                try:
                    with _sqlite_deadline_progress(
                        source_connection,
                        execution_deadline,
                    ), _sqlite_deadline_progress(target, execution_deadline):
                        self._initialize_target(
                            target,
                            max_page_count=max(
                                1,
                                self._maximum_bytes // _SQLITE_PAGE_BYTES,
                            ),
                        )
                        self._copy_content(
                            source=source_connection,
                            target=target,
                            execution_deadline=execution_deadline,
                        )
                        metadata = {
                            "schema": DOMAIN_SUPPLEMENT_SCHEMA,
                            "graph_revision": graph_revision,
                            "source_scope": source_scope,
                            "knowledge_index_id": knowledge_index_id,
                            "source_id": source_id,
                            "source_revision_id": source_revision_id,
                            "source_revision_digest": source_revision_digest,
                            "domain_count": content.domain_count,
                            "semantic_node_count": content.semantic_node_count,
                            "semantic_edge_count": content.semantic_edge_count,
                            "declaration_edge_count": content.declaration_edge_count,
                            "logical_content_hash": content.logical_content_hash,
                        }
                        target.executemany(
                            "INSERT INTO supplement_meta(key, value) "
                            "VALUES (?, ?)",
                            [
                                (key, _canonical_json_text(value))
                                for key, value in sorted(metadata.items())
                            ],
                        )
                        target.commit()
                        target.execute("VACUUM")
                finally:
                    target.close()
            _checkpoint(execution_deadline)
            size_bytes = temporary.stat().st_size
            if size_bytes <= 0 or size_bytes > self._maximum_bytes:
                raise RuntimeError("codecompass_domain_supplement_too_large")
            verified = self.inspect_published(
                temporary,
                execution_deadline=execution_deadline,
            )
            if (
                verified["graph_revision"] != graph_revision
                or verified["graph_content_hash"] != content.logical_content_hash
            ):
                raise RuntimeError("codecompass_domain_supplement_verification_failed")
            os.replace(temporary, destination)
            return {
                **verified,
                "path": str(destination),
                "size_bytes": size_bytes,
            }
        except sqlite3.OperationalError as exc:
            if "full" in str(exc).lower():
                raise RuntimeError(
                    "codecompass_domain_supplement_too_large"
                ) from exc
            raise
        finally:
            if temporary.exists():
                temporary.unlink()

    @classmethod
    def inspect_published(
        cls,
        path: str | Path,
        *,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> dict[str, Any]:
        _checkpoint(execution_deadline)
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("codecompass_domain_supplement_missing")
        size_bytes = candidate.stat().st_size
        if size_bytes <= 0 or size_bytes > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES:
            raise ValueError("codecompass_domain_supplement_too_large")
        with candidate.open("rb") as handle:
            if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
                raise ValueError("codecompass_domain_supplement_format_invalid")
        with closing(
            sqlite3.connect(_read_only_uri(candidate.resolve()), uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            with _sqlite_deadline_progress(connection, execution_deadline):
                if (
                    connection.execute("PRAGMA user_version").fetchone()[0]
                    != DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION
                ):
                    raise ValueError(
                        "codecompass_domain_supplement_schema_invalid"
                    )
                if (
                    connection.execute("PRAGMA application_id").fetchone()[0]
                    != DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID
                ):
                    raise ValueError(
                        "codecompass_domain_supplement_schema_invalid"
                    )
                cls._validate_published_schema(connection)
                metadata = cls._metadata(connection)
                if metadata.get("schema") != DOMAIN_SUPPLEMENT_SCHEMA:
                    raise ValueError(
                        "codecompass_domain_supplement_schema_invalid"
                    )
                graph_revision = str(metadata.get("graph_revision") or "")
                if not _valid_prefixed_sha256(graph_revision):
                    raise ValueError(
                        "codecompass_domain_supplement_graph_revision_invalid"
                    )
                content = cls._logical_content(
                    connection,
                    execution_deadline=execution_deadline,
                )
                if content.logical_content_hash != metadata.get(
                    "logical_content_hash"
                ):
                    raise ValueError(
                        "codecompass_domain_supplement_content_hash_mismatch"
                    )
                expected_counts = {
                    "domain_count": content.domain_count,
                    "semantic_node_count": content.semantic_node_count,
                    "semantic_edge_count": content.semantic_edge_count,
                    "declaration_edge_count": content.declaration_edge_count,
                }
                if any(
                    metadata.get(key) != value
                    for key, value in expected_counts.items()
                ):
                    raise ValueError(
                        "codecompass_domain_supplement_count_mismatch"
                    )
                cls._validate_binding_metadata(metadata)
                return {
                    "artifact_schema": DOMAIN_SUPPLEMENT_SCHEMA,
                    "graph_revision": graph_revision,
                    "graph_content_hash": content.logical_content_hash,
                    "source_scope": str(metadata.get("source_scope") or ""),
                    "knowledge_index_id": str(
                        metadata.get("knowledge_index_id") or ""
                    ),
                    "source_id": str(metadata.get("source_id") or ""),
                    "source_revision_id": str(
                        metadata.get("source_revision_id") or ""
                    ),
                    "source_revision_digest": str(
                        metadata.get("source_revision_digest") or ""
                    ),
                    **expected_counts,
                }

    @staticmethod
    def _initialize_target(
        connection: sqlite3.Connection,
        *,
        max_page_count: int,
    ) -> None:
        connection.execute("PRAGMA page_size=4096")
        connection.execute("PRAGMA auto_vacuum=NONE")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            f"PRAGMA application_id={DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID}"
        )
        connection.execute(
            f"PRAGMA user_version={DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION}"
        )
        connection.execute(f"PRAGMA max_page_count={int(max_page_count)}")
        connection.executescript(
            """
            CREATE TABLE supplement_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE domains (
              domain_key TEXT PRIMARY KEY,
              domain_kind TEXT NOT NULL CHECK(domain_kind IN ('top_level_path', 'repository_root')),
              domain_label TEXT NOT NULL,
              source_file_count INTEGER NOT NULL CHECK(source_file_count >= 1),
              semantic_node_count INTEGER NOT NULL CHECK(semantic_node_count >= 0),
              semantic_edge_count INTEGER NOT NULL CHECK(semantic_edge_count >= 0),
              declaration_edge_count INTEGER NOT NULL CHECK(declaration_edge_count >= 0),
              semantic_node_bytes INTEGER NOT NULL CHECK(semantic_node_bytes >= 0),
              semantic_edge_bytes INTEGER NOT NULL CHECK(semantic_edge_bytes >= 0),
              declaration_edge_bytes INTEGER NOT NULL CHECK(declaration_edge_bytes >= 0),
              complete INTEGER NOT NULL CHECK(complete = 1)
            ) WITHOUT ROWID;
            CREATE TABLE domain_payloads (
              domain_key TEXT NOT NULL REFERENCES domains(domain_key),
              payload_kind TEXT NOT NULL CHECK(payload_kind IN ('nodes', 'semantic_edges', 'declaration_edges')),
              chunk_ordinal INTEGER NOT NULL CHECK(chunk_ordinal >= 0),
              row_count INTEGER NOT NULL CHECK(row_count >= 1),
              raw_size INTEGER NOT NULL CHECK(raw_size >= 1 AND raw_size <= 1048576),
              raw_sha256 TEXT NOT NULL,
              payload_zlib BLOB NOT NULL,
              PRIMARY KEY(domain_key, payload_kind, chunk_ordinal)
            ) WITHOUT ROWID;
            CREATE INDEX idx_domain_payload_kind
              ON domain_payloads(domain_key, payload_kind, chunk_ordinal);
            """
        )

    @staticmethod
    def _validate_published_schema(connection: sqlite3.Connection) -> None:
        objects = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        }
        expected = {
            ("table", "supplement_meta"),
            ("table", "domains"),
            ("table", "domain_payloads"),
            ("index", "idx_domain_payload_kind"),
        }
        if objects != expected:
            raise ValueError("codecompass_domain_supplement_schema_invalid")

    @staticmethod
    def _validate_binding_metadata(metadata: Mapping[str, Any]) -> None:
        expected_keys = {
            "schema",
            "graph_revision",
            "source_scope",
            "knowledge_index_id",
            "source_id",
            "source_revision_id",
            "source_revision_digest",
            "domain_count",
            "semantic_node_count",
            "semantic_edge_count",
            "declaration_edge_count",
            "logical_content_hash",
        }
        if set(metadata) != expected_keys:
            raise ValueError("codecompass_domain_supplement_metadata_invalid")
        if (
            not str(metadata.get("source_scope") or "").strip()
            or not str(metadata.get("knowledge_index_id") or "").strip()
            or not str(metadata.get("source_id") or "").strip()
            or not _valid_source_revision_id(metadata.get("source_revision_id"))
            or not _valid_sha256(metadata.get("source_revision_digest"))
            or str(metadata.get("source_id") or "")
            != f"bound-source:{metadata.get('source_revision_id')}"
        ):
            raise ValueError("codecompass_domain_supplement_binding_invalid")

    @classmethod
    def _copy_content(
        cls,
        *,
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> None:
        for domain in cls._domain_rows(
            source,
            execution_deadline=execution_deadline,
        ):
            _checkpoint(execution_deadline)
            target.execute(
                "INSERT INTO domains VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    domain["domain_key"],
                    domain["domain_kind"],
                    domain["domain_label"],
                    domain["source_file_count"],
                    domain["semantic_node_count"],
                    domain["semantic_edge_count"],
                    domain["declaration_edge_count"],
                    domain["semantic_node_bytes"],
                    domain["semantic_edge_bytes"],
                    domain["declaration_edge_bytes"],
                ),
            )
            for payload_kind in DOMAIN_SUPPLEMENT_PAYLOAD_KINDS:
                records = cls._records(
                    source,
                    domain_key=str(domain["domain_key"]),
                    payload_kind=payload_kind,
                )
                for ordinal, raw, row_count in cls._chunks(records):
                    _checkpoint(execution_deadline)
                    target.execute(
                        "INSERT INTO domain_payloads VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            domain["domain_key"],
                            payload_kind,
                            ordinal,
                            row_count,
                            len(raw),
                            hashlib.sha256(raw).hexdigest(),
                            sqlite3.Binary(zlib.compress(raw, level=9)),
                        ),
                    )
                    _checkpoint(execution_deadline)

    @classmethod
    def _logical_content(
        cls,
        connection: sqlite3.Connection,
        *,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> DomainSupplementContent:
        _checkpoint(execution_deadline)
        digest = hashlib.sha256()
        digest.update(DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX)
        domain_count = 0
        semantic_node_count = 0
        semantic_edge_count = 0
        declaration_edge_count = 0
        raw_payload_bytes = 0
        is_published = cls._has_table(connection, "domain_payloads")
        for domain in cls._domain_rows(
            connection,
            execution_deadline=execution_deadline,
        ):
            _checkpoint(execution_deadline)
            domain_count += 1
            if domain_count > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS:
                raise ValueError(
                    "codecompass_domain_supplement_domain_limit_exceeded"
                )
            semantic_node_count += int(domain["semantic_node_count"])
            semantic_edge_count += int(domain["semantic_edge_count"])
            declaration_edge_count += int(domain["declaration_edge_count"])
            digest.update(
                _canonical_json_bytes(
                    codecompass_domain_supplement_logical_domain(domain)
                )
            )
            digest.update(b"\n")
            for payload_kind in DOMAIN_SUPPLEMENT_PAYLOAD_KINDS:
                if is_published:
                    chunks = cls._published_chunks(
                        connection,
                        domain_key=str(domain["domain_key"]),
                        payload_kind=payload_kind,
                        execution_deadline=execution_deadline,
                    )
                else:
                    chunks = cls._chunks(
                        cls._records(
                            connection,
                            domain_key=str(domain["domain_key"]),
                            payload_kind=payload_kind,
                        )
                    )
                for ordinal, raw, row_count in chunks:
                    _checkpoint(execution_deadline)
                    raw_payload_bytes += len(raw)
                    if (
                        raw_payload_bytes
                        > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES
                    ):
                        raise ValueError(
                            "codecompass_domain_supplement_raw_budget_exceeded"
                        )
                    chunk_header = codecompass_domain_supplement_logical_chunk_header(
                        domain_key=str(domain["domain_key"]),
                        payload_kind=payload_kind,
                        chunk_ordinal=ordinal,
                        row_count=row_count,
                        raw_size=len(raw),
                        raw_sha256=hashlib.sha256(raw).hexdigest(),
                    )
                    digest.update(_canonical_json_bytes(chunk_header))
                    digest.update(b"\n")
                    digest.update(raw)
                    _checkpoint(execution_deadline)
        return DomainSupplementContent(
            logical_content_hash="sha256:" + digest.hexdigest(),
            domain_count=domain_count,
            semantic_node_count=semantic_node_count,
            semantic_edge_count=semantic_edge_count,
            declaration_edge_count=declaration_edge_count,
        )

    @classmethod
    def _domain_rows(
        cls,
        connection: sqlite3.Connection,
        *,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> Iterator[dict[str, Any]]:
        published = cls._has_column(connection, "domains", "complete")
        if published:
            rows = connection.execute(
                "SELECT domain_key, domain_kind, domain_label, source_file_count, "
                "semantic_node_count, semantic_edge_count, declaration_edge_count, "
                "semantic_node_bytes, semantic_edge_bytes, declaration_edge_bytes, complete "
                "FROM domains ORDER BY domain_key"
            )
            for row in rows:
                _checkpoint(execution_deadline)
                if int(row[10]) != 1:
                    raise ValueError("codecompass_domain_supplement_incomplete")
                yield {
                    "domain_key": str(row[0]),
                    "domain_kind": str(row[1]),
                    "domain_label": str(row[2]),
                    "source_file_count": int(row[3]),
                    "semantic_node_count": int(row[4]),
                    "semantic_edge_count": int(row[5]),
                    "declaration_edge_count": int(row[6]),
                    "semantic_node_bytes": int(row[7]),
                    "semantic_edge_bytes": int(row[8]),
                    "declaration_edge_bytes": int(row[9]),
                }
            return

        for row in connection.execute(
            "SELECT domain_key, domain_kind, domain_label, source_file_count "
            "FROM domains ORDER BY domain_key"
        ):
            _checkpoint(execution_deadline)
            domain_key = str(row[0])
            counts_and_bytes = {
                payload_kind: cls._raw_stream_evidence(
                    connection,
                    domain_key=domain_key,
                    payload_kind=payload_kind,
                    execution_deadline=execution_deadline,
                )
                for payload_kind in DOMAIN_SUPPLEMENT_PAYLOAD_KINDS
            }
            yield {
                "domain_key": domain_key,
                "domain_kind": str(row[1]),
                "domain_label": str(row[2]),
                "source_file_count": int(row[3]),
                "semantic_node_count": counts_and_bytes["nodes"][0],
                "semantic_edge_count": counts_and_bytes["semantic_edges"][0],
                "declaration_edge_count": counts_and_bytes["declaration_edges"][0],
                "semantic_node_bytes": counts_and_bytes["nodes"][1],
                "semantic_edge_bytes": counts_and_bytes["semantic_edges"][1],
                "declaration_edge_bytes": counts_and_bytes["declaration_edges"][1],
            }

    @classmethod
    def _raw_stream_evidence(
        cls,
        connection: sqlite3.Connection,
        *,
        domain_key: str,
        payload_kind: str,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> tuple[int, int]:
        count = 0
        byte_count = 0
        for record in cls._records(
            connection,
            domain_key=domain_key,
            payload_kind=payload_kind,
        ):
            if count % 256 == 0:
                _checkpoint(execution_deadline)
            count += 1
            byte_count += len(record) + 1
        _checkpoint(execution_deadline)
        return count, byte_count

    @staticmethod
    def _records(
        connection: sqlite3.Connection,
        *,
        domain_key: str,
        payload_kind: str,
    ) -> Iterator[bytes]:
        table = {
            "nodes": "semantic_nodes",
            "semantic_edges": "semantic_edges",
            "declaration_edges": "declaration_edges",
        }[payload_kind]
        for row in connection.execute(
            f"SELECT record_json FROM {table} WHERE domain_key = ? ORDER BY record_json",
            (domain_key,),
        ):
            yield str(row[0]).encode("utf-8")

    @staticmethod
    def _chunks(records: Iterator[bytes]) -> Iterator[tuple[int, bytes, int]]:
        ordinal = 0
        current = bytearray()
        row_count = 0
        for record in records:
            line = record + b"\n"
            if len(line) > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES:
                raise RuntimeError("codecompass_domain_supplement_record_too_large")
            if current and len(current) + len(line) > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES:
                yield ordinal, bytes(current), row_count
                ordinal += 1
                current = bytearray()
                row_count = 0
            current.extend(line)
            row_count += 1
        if current:
            yield ordinal, bytes(current), row_count

    @staticmethod
    def _published_chunks(
        connection: sqlite3.Connection,
        *,
        domain_key: str,
        payload_kind: str,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> Iterator[tuple[int, bytes, int]]:
        for row in connection.execute(
            "SELECT chunk_ordinal, row_count, raw_size, raw_sha256, payload_zlib "
            "FROM domain_payloads WHERE domain_key = ? AND payload_kind = ? "
            "ORDER BY chunk_ordinal",
            (domain_key, payload_kind),
        ):
            _checkpoint(execution_deadline)
            ordinal = int(row[0])
            row_count = int(row[1])
            raw = _bounded_decompress(bytes(row[4]), expected_size=int(row[2]))
            if hashlib.sha256(raw).hexdigest() != str(row[3]):
                raise ValueError("codecompass_domain_supplement_chunk_hash_mismatch")
            if raw.count(b"\n") != row_count or not raw.endswith(b"\n"):
                raise ValueError("codecompass_domain_supplement_chunk_count_mismatch")
            _checkpoint(execution_deadline)
            yield ordinal, raw, row_count

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key, raw_value in connection.execute(
            "SELECT key, value FROM supplement_meta ORDER BY key"
        ):
            try:
                metadata[str(key)] = codecompass_domain_supplement_decode_metadata(
                    raw_value
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("codecompass_domain_supplement_metadata_invalid") from exc
        return metadata

    @staticmethod
    def _has_table(connection: sqlite3.Connection, table: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _has_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
    ) -> bool:
        return any(
            str(row[1]) == column
            for row in connection.execute(f"PRAGMA table_info({table})")
        )

    @staticmethod
    def _validated_source_path(path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("codecompass_domain_supplement_source_missing")
        size_bytes = candidate.stat().st_size
        if (
            size_bytes <= 0
            or size_bytes > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES
        ):
            raise ValueError("codecompass_domain_supplement_source_too_large")
        resolved = candidate.resolve(strict=True)
        with resolved.open("rb") as handle:
            if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
                raise ValueError("codecompass_domain_supplement_source_invalid")
        return resolved

    @classmethod
    def _connect_source(
        cls,
        path: Path,
        *,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(_read_only_uri(path), uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            with _sqlite_deadline_progress(connection, execution_deadline):
                if (
                    connection.execute("PRAGMA application_id").fetchone()[0]
                    != _SOURCE_SQLITE_APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0]
                    != _SOURCE_SQLITE_USER_VERSION
                ):
                    raise ValueError(
                        "codecompass_domain_supplement_source_schema_invalid"
                    )
                cls._validate_source_schema(connection)
                metadata = connection.execute(
                    "SELECT key, value FROM source_meta ORDER BY key"
                ).fetchall()
                if metadata != [("schema", _SOURCE_SCHEMA)]:
                    raise ValueError(
                        "codecompass_domain_supplement_source_schema_invalid"
                    )
                quick_check = connection.execute("PRAGMA quick_check").fetchall()
                if quick_check != [("ok",)]:
                    raise ValueError(
                        "codecompass_domain_supplement_source_integrity_invalid"
                    )
                incomplete = connection.execute(
                    "SELECT 1 FROM incomplete_domains LIMIT 1"
                ).fetchone()
                if incomplete is not None:
                    raise ValueError(
                        "codecompass_domain_supplement_source_incomplete"
                    )
            return connection
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _validate_source_schema(connection: sqlite3.Connection) -> None:
        objects = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        }
        expected_objects = {
            ("table", table) for table in _SOURCE_TABLE_COLUMNS
        }
        if objects != expected_objects:
            raise ValueError(
                "codecompass_domain_supplement_source_schema_invalid"
            )
        for table, expected_columns in _SOURCE_TABLE_COLUMNS.items():
            columns = tuple(
                (
                    str(row[1]),
                    str(row[2]).upper(),
                    int(row[3]),
                    int(row[5]),
                )
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if columns != expected_columns:
                raise ValueError(
                    "codecompass_domain_supplement_source_schema_invalid"
                )
        for table in (
            "semantic_nodes",
            "semantic_edges",
            "declaration_edges",
            "incomplete_domains",
        ):
            foreign_keys = connection.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall()
            if len(foreign_keys) != 1 or tuple(foreign_keys[0][2:5]) != (
                "domains",
                "domain_key",
                "domain_key",
            ):
                raise ValueError(
                    "codecompass_domain_supplement_source_schema_invalid"
                )


__all__ = [
    "CodeCompassDomainSupplementExecutionDeadlinePort",
    "DOMAIN_SUPPLEMENT_FILENAME",
    "DOMAIN_SUPPLEMENT_MEDIA_TYPE",
    "DOMAIN_SUPPLEMENT_SCHEMA",
    "DOMAIN_SUPPLEMENT_SOURCE_FILENAME",
    "CodeCompassDomainSupplementSourceWriter",
    "DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES",
    "DomainSupplementContent",
    "MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES",
    "SemanticDomainIdentity",
    "WorkerCodeCompassDomainSupplementMaterializer",
]
