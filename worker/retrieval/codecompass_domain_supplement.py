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
import zlib
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_PAYLOAD_KINDS,
    DOMAIN_SUPPLEMENT_SCHEMA,
    DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID,
    DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION,
    codecompass_domain_supplement_decode_metadata,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
)
from worker.retrieval.codecompass_domain_supplement_config import (
    DEFAULT_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES,
)
from worker.retrieval.codecompass_domain_supplement_content_reader import (
    DomainSupplementLogicalContentReader,
)
from worker.retrieval.codecompass_domain_supplement_primitives import (
    _SOURCE_SCHEMA,
    _SOURCE_SQLITE_APPLICATION_ID,
    _SOURCE_SQLITE_USER_VERSION,
    _SOURCE_TABLE_COLUMNS,
    _SQLITE_HEADER,
    _SQLITE_PAGE_BYTES,
    DOMAIN_SUPPLEMENT_SOURCE_FILENAME,
    CodeCompassDomainSupplementExecutionDeadlinePort,
    DomainSupplementContent,
    SemanticDomainIdentity,
    _canonical_json_text,
    _checkpoint,
    _read_only_uri,
    _sqlite_deadline_progress,
    _valid_prefixed_sha256,
    _valid_sha256,
    _valid_source_revision_id,
)
from worker.retrieval.codecompass_domain_supplement_source_writer import (
    CodeCompassDomainSupplementSourceWriter,
)

# Compatibility re-exports: names that were importable from this module
# before its collaborators were extracted.
from ananta_contracts.codecompass_domain_supplement import (  # noqa: E402,F401,I001
    codecompass_domain_supplement_canonical_json_bytes,
    codecompass_domain_supplement_encode_metadata,
    codecompass_domain_supplement_logical_chunk_header,
    codecompass_domain_supplement_logical_domain,
)
from ananta_contracts.codecompass_graph_limits import (  # noqa: E402,F401
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (  # noqa: E402,F401
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)
from worker.retrieval.codecompass_domain_supplement_config import (  # noqa: E402,F401
    configured_domain_supplement_source_bytes,
    validate_domain_supplement_source_bytes,
)
from worker.retrieval.codecompass_domain_supplement_content_reader import (  # noqa: E402,F401
    DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX,
)
from worker.retrieval.codecompass_domain_supplement_primitives import (  # noqa: E402,F401
    _DOMAIN_KINDS,
    _SQLITE_PROGRESS_OPCODES,
    _bounded_decompress,
    _canonical_json_bytes,
    _prefixed_sha256,
)


class WorkerCodeCompassDomainSupplementMaterializer:
    """Compress and bind a complete raw semantic source to one graph revision."""

    def __init__(
        self,
        *,
        maximum_bytes: int = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
        content_reader: DomainSupplementLogicalContentReader | None = None,
    ) -> None:
        if maximum_bytes <= 0 or maximum_bytes > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES:
            raise ValueError("codecompass_domain_supplement_limit_invalid")
        self._maximum_bytes = int(maximum_bytes)
        self._content_reader = (
            content_reader or DomainSupplementLogicalContentReader()
        )

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
                return self._content_reader.logical_content(
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
                    content = self._content_reader.logical_content(
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
            verified = self._inspect_published_with(
                temporary,
                content_reader=self._content_reader,
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
        return cls._inspect_published_with(
            path,
            content_reader=DomainSupplementLogicalContentReader(),
            execution_deadline=execution_deadline,
        )

    @classmethod
    def _inspect_published_with(
        cls,
        path: str | Path,
        *,
        content_reader: DomainSupplementLogicalContentReader,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ),
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
                content = content_reader.logical_content(
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

    def _copy_content(
        self,
        *,
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        execution_deadline: (
            CodeCompassDomainSupplementExecutionDeadlinePort | None
        ) = None,
    ) -> None:
        for domain in self._content_reader.domain_rows(
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
                records = self._content_reader.records(
                    source,
                    domain_key=str(domain["domain_key"]),
                    payload_kind=payload_kind,
                )
                for ordinal, raw, row_count in self._content_reader.chunks(records):
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
    "DomainSupplementLogicalContentReader",
    "MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_BYTES",
    "SemanticDomainIdentity",
    "WorkerCodeCompassDomainSupplementMaterializer",
]
