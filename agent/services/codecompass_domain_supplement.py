"""Read-only access to revision-bound CodeCompass domain supplements."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zlib
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Protocol

from agent.services.artifact_integrity_verifier import (
    ArtifactIntegrityVerifierPort,
    get_artifact_integrity_verifier,
)
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
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)

_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_DOMAIN_KEY = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_COMPRESSED_CHUNK_BYTES = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES + 128 * 1024
_MAX_VALIDATION_RAW_BYTES = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES
_MAX_SELECTED_RAW_BYTES = _MAX_VALIDATION_RAW_BYTES
_DEFAULT_CACHE_BYTES = 64 * 1024 * 1024
_MAX_IDENTIFIER_CHARACTERS = 4_096
_SQLITE_PROGRESS_OPCODES = 10_000
_TABLES = frozenset({"supplement_meta", "domains", "domain_payloads"})
_PAYLOAD_KINDS = frozenset(DOMAIN_SUPPLEMENT_PAYLOAD_KINDS)
_EXPECTED_SCHEMA_SQL = {
    "supplement_meta": ("CREATE TABLE supplement_meta ( key TEXT PRIMARY KEY, value TEXT NOT NULL ) WITHOUT ROWID"),
    "domains": (
        "CREATE TABLE domains ( domain_key TEXT PRIMARY KEY, "
        "domain_kind TEXT NOT NULL CHECK(domain_kind IN "
        "('top_level_path', 'repository_root')), domain_label TEXT NOT NULL, "
        "source_file_count INTEGER NOT NULL CHECK(source_file_count >= 1), "
        "semantic_node_count INTEGER NOT NULL CHECK(semantic_node_count >= 0), "
        "semantic_edge_count INTEGER NOT NULL CHECK(semantic_edge_count >= 0), "
        "declaration_edge_count INTEGER NOT NULL "
        "CHECK(declaration_edge_count >= 0), semantic_node_bytes INTEGER NOT "
        "NULL CHECK(semantic_node_bytes >= 0), semantic_edge_bytes INTEGER NOT "
        "NULL CHECK(semantic_edge_bytes >= 0), declaration_edge_bytes INTEGER "
        "NOT NULL CHECK(declaration_edge_bytes >= 0), complete INTEGER NOT NULL "
        "CHECK(complete = 1) ) WITHOUT ROWID"
    ),
    "domain_payloads": (
        "CREATE TABLE domain_payloads ( domain_key TEXT NOT NULL REFERENCES "
        "domains(domain_key), payload_kind TEXT NOT NULL CHECK(payload_kind IN "
        "('nodes', 'semantic_edges', 'declaration_edges')), chunk_ordinal "
        "INTEGER NOT NULL CHECK(chunk_ordinal >= 0), row_count INTEGER NOT NULL "
        "CHECK(row_count >= 1), raw_size INTEGER NOT NULL CHECK(raw_size >= 1 "
        "AND raw_size <= 1048576), raw_sha256 TEXT NOT NULL, payload_zlib BLOB "
        "NOT NULL, PRIMARY KEY(domain_key, payload_kind, chunk_ordinal) ) "
        "WITHOUT ROWID"
    ),
    "idx_domain_payload_kind": (
        "CREATE INDEX idx_domain_payload_kind ON domain_payloads(domain_key, payload_kind, chunk_ordinal)"
    ),
}


def _canonical_json(value: object) -> str:
    return codecompass_domain_supplement_canonical_json_bytes(value).decode("utf-8")


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").split())


def _checkpoint(callback: Callable[[], object] | None) -> None:
    if callback is not None:
        callback()


@contextmanager
def _sqlite_checkpoint_progress(
    connection: sqlite3.Connection,
    callback: Callable[[], object] | None,
) -> Iterator[None]:
    """Interrupt long read-only SQLite work through a caller-owned deadline."""

    if callback is None:
        yield
        return
    failure: list[Exception] = []

    def progress() -> int:
        try:
            callback()
        except Exception as exc:  # SQLite callbacks cannot propagate directly.
            if not failure:
                failure.append(exc)
            return 1
        return 0

    connection.set_progress_handler(progress, _SQLITE_PROGRESS_OPCODES)
    try:
        _checkpoint(callback)
        try:
            yield
        except sqlite3.OperationalError as exc:
            if failure:
                raise failure[0] from exc
            raise
        if failure:
            raise failure[0]
        _checkpoint(callback)
    finally:
        connection.set_progress_handler(None, 0)


class CodeCompassDomainSupplementError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class CodeCompassDomainSupplementBinding:
    knowledge_index_id: str
    source_revision_id: str
    source_revision_digest: str
    graph_revision: str
    artifact_sha256: str
    logical_content_hash: str
    source_scope: str = "repo_path"
    source_id: str | None = None


@dataclass(frozen=True)
class CodeCompassDomainSupplementSummary:
    domain_key: str
    domain_kind: str
    domain_label: str
    source_file_count: int
    semantic_node_count: int
    semantic_edge_count: int
    declaration_edge_count: int
    semantic_node_bytes: int
    semantic_edge_bytes: int
    declaration_edge_bytes: int
    complete: bool


@dataclass(frozen=True)
class CodeCompassDomainSupplementCatalog:
    graph_revision: str
    logical_content_hash: str
    domains: tuple[CodeCompassDomainSupplementSummary, ...]


@dataclass(frozen=True)
class CodeCompassDomainSupplementRecords:
    graph_revision: str
    logical_content_hash: str
    domain_keys: tuple[str, ...]
    nodes: tuple[Mapping[str, object], ...]
    semantic_edges: tuple[Mapping[str, object], ...]
    declaration_edges: tuple[Mapping[str, object], ...]
    semantic_node_count: int
    semantic_edge_count: int
    declaration_edge_count: int


@dataclass(frozen=True)
class _CachedDomain:
    records: tuple[
        tuple[Mapping[str, object], ...],
        tuple[Mapping[str, object], ...],
        tuple[Mapping[str, object], ...],
    ]
    raw_size: int


class CodeCompassDomainSupplementPort(Protocol):
    def validate_artifact(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog: ...

    def catalog(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog: ...

    def load_domains(
        self,
        *,
        path: Path,
        domain_keys: Sequence[str],
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementRecords: ...


class SqliteCodeCompassDomainSupplementReader:
    """Validate and lazily read immutable SQLite domain shards.

    SQLite is opened in immutable read-only mode. Schema, metadata, row counts,
    decompressed byte budgets, hashes and canonical JSONL are all checked before
    records are returned to the graph read path.
    """

    def __init__(
        self,
        *,
        integrity: ArtifactIntegrityVerifierPort | None = None,
        maximum_cached_domains: int = 4,
        maximum_cached_bytes: int = _DEFAULT_CACHE_BYTES,
    ) -> None:
        if maximum_cached_domains < 1:
            raise ValueError("domain_supplement_cache_size_invalid")
        if not 1 <= maximum_cached_bytes <= MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES:
            raise ValueError("domain_supplement_cache_byte_limit_invalid")
        self._integrity = integrity or get_artifact_integrity_verifier()
        self._maximum_cached_domains = int(maximum_cached_domains)
        self._maximum_cached_bytes = int(maximum_cached_bytes)
        self._cached_bytes = 0
        self._domain_cache: OrderedDict[
            tuple[str, str, str],
            _CachedDomain,
        ] = OrderedDict()
        self._cache_lock = RLock()

    def validate_artifact(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog:
        _checkpoint(checkpoint)
        self._verify_file(
            path=path,
            binding=binding,
            checkpoint=checkpoint,
        )
        with self._connection(path, checkpoint=checkpoint) as connection:
            catalog, metadata = self._validated_catalog(
                connection=connection,
                binding=binding,
            )
            logical_hash = hashlib.sha256(DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX)
            total_raw_bytes = 0
            summaries = {item.domain_key: item for item in catalog.domains}
            observed: dict[str, dict[str, list[int]]] = {
                key: {kind: [0, 0, 0] for kind in _PAYLOAD_KINDS} for key in summaries
            }
            for summary in catalog.domains:
                _checkpoint(checkpoint)
                logical_hash.update(self._domain_logical_line(summary))
                for payload_kind in DOMAIN_SUPPLEMENT_PAYLOAD_KINDS:
                    for row in self._chunk_rows(
                        connection,
                        domain_keys=(summary.domain_key,),
                        payload_kind=payload_kind,
                    ):
                        _checkpoint(checkpoint)
                        domain_key, kind, ordinal, row_count, raw = self._validated_chunk(row)
                        counters = observed[domain_key][kind]
                        if ordinal != counters[0]:
                            raise CodeCompassDomainSupplementError("domain_supplement_chunk_ordinal_invalid")
                        counters[0] += 1
                        counters[1] += row_count
                        counters[2] += len(raw)
                        total_raw_bytes += len(raw)
                        if total_raw_bytes > _MAX_VALIDATION_RAW_BYTES:
                            raise CodeCompassDomainSupplementError("domain_supplement_raw_budget_exceeded")
                        self._parse_records(
                            raw=raw,
                            row_count=row_count,
                            payload_kind=kind,
                            domain_key=domain_key,
                        )
                        logical_hash.update(self._chunk_logical_line(row))
                        logical_hash.update(raw)
            _checkpoint(checkpoint)
            self._assert_stream_counts(
                catalog=catalog,
                observed=observed,
            )
            actual_logical_hash = f"sha256:{logical_hash.hexdigest()}"
            if (
                actual_logical_hash != binding.logical_content_hash
                or actual_logical_hash != metadata["logical_content_hash"]
            ):
                raise CodeCompassDomainSupplementError("domain_supplement_logical_hash_mismatch")
            return catalog

    def catalog(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementCatalog:
        _checkpoint(checkpoint)
        self._verify_file(
            path=path,
            binding=binding,
            checkpoint=checkpoint,
        )
        with self._connection(path, checkpoint=checkpoint) as connection:
            catalog, _metadata = self._validated_catalog(
                connection=connection,
                binding=binding,
            )
            return catalog

    def load_domains(
        self,
        *,
        path: Path,
        domain_keys: Sequence[str],
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> CodeCompassDomainSupplementRecords:
        _checkpoint(checkpoint)
        requested = tuple(sorted(set(domain_keys)))
        if not requested or any(_DOMAIN_KEY.fullmatch(key) is None for key in requested):
            raise CodeCompassDomainSupplementError("domain_supplement_selector_invalid")
        self._verify_file(
            path=path,
            binding=binding,
            checkpoint=checkpoint,
        )
        with self._connection(path, checkpoint=checkpoint) as connection:
            catalog, _metadata = self._validated_catalog(
                connection=connection,
                binding=binding,
            )
            summaries = {item.domain_key: item for item in catalog.domains}
            selected = [summaries[key] for key in requested if key in summaries]
            if not selected:
                return CodeCompassDomainSupplementRecords(
                    graph_revision=catalog.graph_revision,
                    logical_content_hash=catalog.logical_content_hash,
                    domain_keys=(),
                    nodes=(),
                    semantic_edges=(),
                    declaration_edges=(),
                    semantic_node_count=0,
                    semantic_edge_count=0,
                    declaration_edge_count=0,
                )

            nodes: list[Mapping[str, object]] = []
            semantic_edges: list[Mapping[str, object]] = []
            declaration_edges: list[Mapping[str, object]] = []
            expected_selected_raw_bytes = sum(
                item.semantic_node_bytes + item.semantic_edge_bytes + item.declaration_edge_bytes for item in selected
            )
            if expected_selected_raw_bytes > _MAX_SELECTED_RAW_BYTES:
                raise CodeCompassDomainSupplementError("domain_supplement_selected_budget_exceeded")
            for summary in selected:
                _checkpoint(checkpoint)
                cached = self._cached_domain(
                    binding=binding,
                    domain_key=summary.domain_key,
                )
                if cached is None:
                    records, raw_bytes = self._load_domain(
                        connection=connection,
                        summary=summary,
                        checkpoint=checkpoint,
                    )
                    self._cache_domain(
                        binding=binding,
                        domain_key=summary.domain_key,
                        records=records,
                        raw_size=raw_bytes,
                    )
                else:
                    records = cached.records
                domain_nodes, domain_semantic, domain_declarations = records
                nodes.extend(domain_nodes)
                semantic_edges.extend(domain_semantic)
                declaration_edges.extend(domain_declarations)
            _checkpoint(checkpoint)
            return CodeCompassDomainSupplementRecords(
                graph_revision=catalog.graph_revision,
                logical_content_hash=catalog.logical_content_hash,
                domain_keys=tuple(item.domain_key for item in selected),
                nodes=tuple(nodes),
                semantic_edges=tuple(semantic_edges),
                declaration_edges=tuple(declaration_edges),
                semantic_node_count=sum(item.semantic_node_count for item in selected),
                semantic_edge_count=sum(item.semantic_edge_count for item in selected),
                declaration_edge_count=sum(item.declaration_edge_count for item in selected),
            )

    def _verify_file(
        self,
        *,
        path: Path,
        binding: CodeCompassDomainSupplementBinding,
        checkpoint: Callable[[], object] | None = None,
    ) -> None:
        if path.name != DOMAIN_SUPPLEMENT_FILENAME:
            raise CodeCompassDomainSupplementError("domain_supplement_path_invalid")
        try:
            options = (
                {"checkpoint": checkpoint}
                if checkpoint is not None
                else {}
            )
            self._integrity.verify(
                path=path,
                expected_sha256=binding.artifact_sha256,
                maximum_bytes=MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
                **options,
            )
        except (OSError, ValueError) as exc:
            raise CodeCompassDomainSupplementError("domain_supplement_integrity_invalid") from exc

    @contextmanager
    def _connection(
        self,
        path: Path,
        *,
        checkpoint: Callable[[], object] | None = None,
    ) -> Iterator[sqlite3.Connection]:
        try:
            uri = f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
            connection = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            if hasattr(connection, "setlimit"):
                connection.setlimit(
                    sqlite3.SQLITE_LIMIT_LENGTH,
                    _MAX_COMPRESSED_CHUNK_BYTES,
                )
                connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
                connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 64)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 64 * 1024)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            with _sqlite_checkpoint_progress(connection, checkpoint):
                yield connection
        except (OSError, sqlite3.Error) as exc:
            raise CodeCompassDomainSupplementError("domain_supplement_sqlite_invalid") from exc
        finally:
            if "connection" in locals():
                connection.close()

    def _validated_catalog(
        self,
        *,
        connection: sqlite3.Connection,
        binding: CodeCompassDomainSupplementBinding,
    ) -> tuple[CodeCompassDomainSupplementCatalog, dict[str, object]]:
        self._validate_schema(connection)
        metadata = self._metadata(connection)
        self._validate_metadata(metadata=metadata, binding=binding)
        summaries = tuple(
            self._summary(row)
            for row in connection.execute(
                "SELECT domain_key,domain_kind,source_file_count,"
                "domain_label,"
                "semantic_node_count,semantic_edge_count,"
                "declaration_edge_count,semantic_node_bytes,"
                "semantic_edge_bytes,declaration_edge_bytes,complete "
                "FROM domains ORDER BY domain_key"
            )
        )
        if len(summaries) != self._metadata_count(metadata, "domain_count"):
            raise CodeCompassDomainSupplementError("domain_supplement_domain_count_mismatch")
        if len(summaries) > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS:
            raise CodeCompassDomainSupplementError("domain_supplement_domain_limit_exceeded")
        expected_totals = {
            "semantic_node_count": sum(item.semantic_node_count for item in summaries),
            "semantic_edge_count": sum(item.semantic_edge_count for item in summaries),
            "declaration_edge_count": sum(item.declaration_edge_count for item in summaries),
        }
        if any(self._metadata_count(metadata, key) != value for key, value in expected_totals.items()):
            raise CodeCompassDomainSupplementError("domain_supplement_record_count_mismatch")
        return (
            CodeCompassDomainSupplementCatalog(
                graph_revision=metadata["graph_revision"],
                logical_content_hash=metadata["logical_content_hash"],
                domains=summaries,
            ),
            metadata,
        )

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        application_id = connection.execute("PRAGMA application_id").fetchone()
        if application_id is None or application_id[0] != DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID:
            raise CodeCompassDomainSupplementError("domain_supplement_application_id_invalid")
        user_version = connection.execute("PRAGMA user_version").fetchone()
        if user_version is None or user_version[0] != DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION:
            raise CodeCompassDomainSupplementError("domain_supplement_schema_version_invalid")
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise CodeCompassDomainSupplementError("domain_supplement_integrity_check_failed")
        schema_rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        tables = {str(row[1]) for row in schema_rows if str(row[0]) == "table"}
        if tables != _TABLES:
            raise CodeCompassDomainSupplementError("domain_supplement_schema_tables_invalid")
        for row in schema_rows:
            object_type, name, table, sql = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                row[3],
            )
            if object_type == "table":
                if _normalized_sql(sql) != _EXPECTED_SCHEMA_SQL.get(name):
                    raise CodeCompassDomainSupplementError("domain_supplement_schema_definition_invalid")
                continue
            if name == "idx_domain_payload_kind":
                if (
                    object_type != "index"
                    or table != "domain_payloads"
                    or _normalized_sql(sql) != _EXPECTED_SCHEMA_SQL[name]
                ):
                    raise CodeCompassDomainSupplementError("domain_supplement_schema_object_invalid")
                continue
            if not (
                object_type == "index" and name.startswith("sqlite_autoindex_") and table in _TABLES and sql is None
            ):
                raise CodeCompassDomainSupplementError("domain_supplement_schema_object_invalid")
        expected_columns = {
            "supplement_meta": (
                ("key", "TEXT", 1, 1),
                ("value", "TEXT", 1, 0),
            ),
            "domains": (
                ("domain_key", "TEXT", 1, 1),
                ("domain_kind", "TEXT", 1, 0),
                ("domain_label", "TEXT", 1, 0),
                ("source_file_count", "INTEGER", 1, 0),
                ("semantic_node_count", "INTEGER", 1, 0),
                ("semantic_edge_count", "INTEGER", 1, 0),
                ("declaration_edge_count", "INTEGER", 1, 0),
                ("semantic_node_bytes", "INTEGER", 1, 0),
                ("semantic_edge_bytes", "INTEGER", 1, 0),
                ("declaration_edge_bytes", "INTEGER", 1, 0),
                ("complete", "INTEGER", 1, 0),
            ),
            "domain_payloads": (
                ("domain_key", "TEXT", 1, 1),
                ("payload_kind", "TEXT", 1, 2),
                ("chunk_ordinal", "INTEGER", 1, 3),
                ("row_count", "INTEGER", 1, 0),
                ("raw_size", "INTEGER", 1, 0),
                ("raw_sha256", "TEXT", 1, 0),
                ("payload_zlib", "BLOB", 1, 0),
            ),
        }
        for table, expected in expected_columns.items():
            actual = tuple(
                (
                    str(row[1]),
                    str(row[2]).upper(),
                    int(row[3]),
                    int(row[5]),
                )
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != expected:
                raise CodeCompassDomainSupplementError("domain_supplement_schema_columns_invalid")
        foreign_keys = connection.execute("PRAGMA foreign_key_list(domain_payloads)").fetchall()
        if len(foreign_keys) != 1 or (
            str(foreign_keys[0][2]),
            str(foreign_keys[0][3]),
            str(foreign_keys[0][4]),
        ) != ("domains", "domain_key", "domain_key"):
            raise CodeCompassDomainSupplementError("domain_supplement_schema_foreign_key_invalid")
        index_columns = tuple(str(row[2]) for row in connection.execute("PRAGMA index_info(idx_domain_payload_kind)"))
        if index_columns != (
            "domain_key",
            "payload_kind",
            "chunk_ordinal",
        ):
            raise CodeCompassDomainSupplementError("domain_supplement_schema_index_invalid")

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, object]:
        rows = connection.execute("SELECT key,value FROM supplement_meta ORDER BY key").fetchall()
        try:
            metadata: dict[str, object] = {}
            for row in rows:
                key = str(row[0])
                encoded = str(row[1])
                decoded = codecompass_domain_supplement_decode_metadata(encoded)
                if encoded != _canonical_json(decoded):
                    raise ValueError("metadata_not_canonical")
                metadata[key] = decoded
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CodeCompassDomainSupplementError("domain_supplement_metadata_invalid") from exc
        required = {
            "schema",
            "graph_revision",
            "source_scope",
            "knowledge_index_id",
            "source_revision_id",
            "source_revision_digest",
            "source_id",
            "domain_count",
            "semantic_node_count",
            "semantic_edge_count",
            "declaration_edge_count",
            "logical_content_hash",
        }
        if len(metadata) != len(rows) or set(metadata) != required:
            raise CodeCompassDomainSupplementError("domain_supplement_metadata_fields_invalid")
        return metadata

    @classmethod
    def _validate_metadata(
        cls,
        *,
        metadata: Mapping[str, object],
        binding: CodeCompassDomainSupplementBinding,
    ) -> None:
        expected = {
            "schema": DOMAIN_SUPPLEMENT_SCHEMA,
            "graph_revision": binding.graph_revision,
            "source_scope": binding.source_scope,
            "knowledge_index_id": binding.knowledge_index_id,
            "source_revision_id": binding.source_revision_id,
            "source_revision_digest": binding.source_revision_digest,
            "logical_content_hash": binding.logical_content_hash,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise CodeCompassDomainSupplementError("domain_supplement_binding_mismatch")
        source_id = metadata.get("source_id")
        if not isinstance(source_id, str) or (binding.source_id is not None and source_id != binding.source_id):
            raise CodeCompassDomainSupplementError("domain_supplement_source_binding_mismatch")
        source_revision_id = metadata.get("source_revision_id")
        knowledge_index_id = metadata.get("knowledge_index_id")
        source_scope = metadata.get("source_scope")
        if (
            not isinstance(source_revision_id, str)
            or len(source_revision_id) != 69
            or not source_revision_id.startswith("srev_")
            or re.fullmatch(r"[0-9a-f]{64}", source_revision_id[5:]) is None
            or source_id != f"bound-source:{source_revision_id}"
            or not isinstance(knowledge_index_id, str)
            or not knowledge_index_id
            or len(knowledge_index_id) > _MAX_IDENTIFIER_CHARACTERS
            or not isinstance(source_scope, str)
            or not source_scope
            or len(source_scope) > _MAX_IDENTIFIER_CHARACTERS
        ):
            raise CodeCompassDomainSupplementError(
                "domain_supplement_source_binding_mismatch"
            )
        for digest in (
            metadata["graph_revision"],
            metadata["logical_content_hash"],
        ):
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None or not digest.startswith("sha256:"):
                raise CodeCompassDomainSupplementError("domain_supplement_digest_invalid")
        source_revision_digest = metadata["source_revision_digest"]
        if not isinstance(source_revision_digest, str) or re.fullmatch(r"[0-9a-f]{64}", source_revision_digest) is None:
            raise CodeCompassDomainSupplementError("domain_supplement_revision_digest_invalid")
        for key in (
            "domain_count",
            "semantic_node_count",
            "semantic_edge_count",
            "declaration_edge_count",
        ):
            cls._metadata_count(metadata, key)

    @staticmethod
    def _metadata_count(metadata: Mapping[str, object], key: str) -> int:
        raw = metadata.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise CodeCompassDomainSupplementError("domain_supplement_metadata_count_invalid")
        return raw

    @staticmethod
    def _summary(row: sqlite3.Row) -> CodeCompassDomainSupplementSummary:
        domain_key = str(row["domain_key"])
        domain_kind = str(row["domain_kind"])
        domain_label = row["domain_label"]
        valid_identity = (
            domain_kind == "repository_root"
            and domain_label == ""
            and domain_key == codecompass_semantic_repository_root_domain_key()
        ) or (
            domain_kind == "top_level_path"
            and isinstance(domain_label, str)
            and 1 <= len(domain_label) <= _MAX_IDENTIFIER_CHARACTERS
            and domain_key == codecompass_semantic_domain_key(domain_label)
        )
        if _DOMAIN_KEY.fullmatch(domain_key) is None or not valid_identity:
            raise CodeCompassDomainSupplementError("domain_supplement_domain_identity_invalid")
        values: dict[str, int] = {}
        for field in (
            "source_file_count",
            "semantic_node_count",
            "semantic_edge_count",
            "declaration_edge_count",
            "semantic_node_bytes",
            "semantic_edge_bytes",
            "declaration_edge_bytes",
        ):
            value = row[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CodeCompassDomainSupplementError("domain_supplement_domain_count_invalid")
            values[field] = value
        if values["source_file_count"] < 1 or any(
            values[field] > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES
            for field in (
                "source_file_count",
                "semantic_node_count",
                "semantic_edge_count",
                "declaration_edge_count",
            )
        ):
            raise CodeCompassDomainSupplementError("domain_supplement_domain_count_invalid")
        if any(
            values[field] > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES
            for field in (
                "semantic_node_bytes",
                "semantic_edge_bytes",
                "declaration_edge_bytes",
            )
        ):
            raise CodeCompassDomainSupplementError("domain_supplement_domain_byte_count_invalid")
        if row["complete"] != 1:
            raise CodeCompassDomainSupplementError("domain_supplement_domain_incomplete")
        return CodeCompassDomainSupplementSummary(
            domain_key=domain_key,
            domain_kind=domain_kind,
            domain_label=domain_label,
            complete=True,
            **values,
        )

    @staticmethod
    def _chunk_rows(
        connection: sqlite3.Connection,
        *,
        domain_keys: Sequence[str] | None,
        payload_kind: str | None = None,
    ) -> Iterator[sqlite3.Row]:
        if payload_kind is not None and payload_kind not in _PAYLOAD_KINDS:
            raise CodeCompassDomainSupplementError("domain_supplement_internal_selector_invalid")
        if domain_keys is None:
            if payload_kind is not None:
                raise CodeCompassDomainSupplementError("domain_supplement_internal_selector_invalid")
            cursor = connection.execute(
                "SELECT domain_key,payload_kind,chunk_ordinal,row_count,"
                "raw_size,raw_sha256,payload_zlib FROM domain_payloads "
                "ORDER BY domain_key,payload_kind,chunk_ordinal"
            )
        else:
            if len(domain_keys) != 1:
                raise CodeCompassDomainSupplementError("domain_supplement_internal_selector_invalid")
            if payload_kind is None:
                cursor = connection.execute(
                    "SELECT domain_key,payload_kind,chunk_ordinal,row_count,"
                    "raw_size,raw_sha256,payload_zlib FROM domain_payloads "
                    "WHERE domain_key=? ORDER BY payload_kind,chunk_ordinal",
                    (domain_keys[0],),
                )
            else:
                cursor = connection.execute(
                    "SELECT domain_key,payload_kind,chunk_ordinal,row_count,"
                    "raw_size,raw_sha256,payload_zlib FROM domain_payloads "
                    "WHERE domain_key=? AND payload_kind=? "
                    "ORDER BY chunk_ordinal",
                    (domain_keys[0], payload_kind),
                )
        yield from cursor

    @staticmethod
    def _validated_chunk(
        row: sqlite3.Row,
    ) -> tuple[str, str, int, int, bytes]:
        domain_key = str(row["domain_key"])
        payload_kind = str(row["payload_kind"])
        ordinal = row["chunk_ordinal"]
        row_count = row["row_count"]
        raw_size = row["raw_size"]
        raw_sha256 = str(row["raw_sha256"])
        compressed = row["payload_zlib"]
        if (
            _DOMAIN_KEY.fullmatch(domain_key) is None
            or payload_kind not in _PAYLOAD_KINDS
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 0
            or isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count < 1
            or isinstance(raw_size, bool)
            or not isinstance(raw_size, int)
            or not 1 <= raw_size <= MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES
            or re.fullmatch(r"[0-9a-f]{64}", raw_sha256) is None
            or not isinstance(compressed, bytes)
            or not compressed
            or len(compressed) > _MAX_COMPRESSED_CHUNK_BYTES
        ):
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_invalid")
        try:
            decompressor = zlib.decompressobj()
            raw = decompressor.decompress(compressed, raw_size + 1)
            if len(raw) > raw_size or decompressor.unconsumed_tail or not decompressor.eof or decompressor.unused_data:
                raise CodeCompassDomainSupplementError("domain_supplement_chunk_decompression_invalid")
            remaining = raw_size - len(raw)
            raw += decompressor.flush(remaining + 1)
        except zlib.error as exc:
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_decompression_invalid") from exc
        if len(raw) != raw_size:
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_decompression_invalid")
        if hashlib.sha256(raw).hexdigest() != raw_sha256:
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_hash_mismatch")
        return domain_key, payload_kind, ordinal, row_count, raw

    @staticmethod
    def _parse_records(
        *,
        raw: bytes,
        row_count: int,
        payload_kind: str,
        domain_key: str,
    ) -> tuple[Mapping[str, object], ...]:
        if not raw.endswith(b"\n"):
            raise CodeCompassDomainSupplementError("domain_supplement_jsonl_invalid")
        lines = raw[:-1].split(b"\n")
        if len(lines) != row_count or any(not line for line in lines):
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_row_count_mismatch")
        records: list[Mapping[str, object]] = []

        def reject_constant(_value: str) -> None:
            raise ValueError("non_finite_json_number")

        for line in lines:
            try:
                record = json.loads(
                    line.decode("utf-8"),
                    parse_constant=reject_constant,
                )
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise CodeCompassDomainSupplementError("domain_supplement_jsonl_invalid") from exc
            if not isinstance(record, dict) or (_canonical_json(record) + "\n").encode("utf-8") != line + b"\n":
                raise CodeCompassDomainSupplementError("domain_supplement_jsonl_noncanonical")
            marker = record.get(CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD)
            if marker != domain_key:
                raise CodeCompassDomainSupplementError("domain_supplement_record_domain_mismatch")
            if payload_kind == "nodes":
                identifier = record.get("id") or record.get("node_id")
                if not isinstance(identifier, str) or not identifier or len(identifier) > _MAX_IDENTIFIER_CHARACTERS:
                    raise CodeCompassDomainSupplementError("domain_supplement_node_invalid")
            else:
                source = record.get("source") or record.get("source_id")
                target = record.get("target") or record.get("target_id")
                if (
                    not isinstance(source, str)
                    or not source
                    or not isinstance(target, str)
                    or not target
                    or len(source) > _MAX_IDENTIFIER_CHARACTERS
                    or len(target) > _MAX_IDENTIFIER_CHARACTERS
                ):
                    raise CodeCompassDomainSupplementError("domain_supplement_edge_invalid")
            records.append(record)
        return tuple(records)

    def _load_domain(
        self,
        *,
        connection: sqlite3.Connection,
        summary: CodeCompassDomainSupplementSummary,
        checkpoint: Callable[[], object] | None = None,
    ) -> tuple[
        tuple[
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
        ],
        int,
    ]:
        records: dict[str, list[Mapping[str, object]]] = {kind: [] for kind in _PAYLOAD_KINDS}
        expected_ordinal = {kind: 0 for kind in _PAYLOAD_KINDS}
        raw_bytes = 0
        for row in self._chunk_rows(
            connection,
            domain_keys=(summary.domain_key,),
        ):
            _checkpoint(checkpoint)
            domain_key, kind, ordinal, row_count, raw = self._validated_chunk(row)
            if domain_key != summary.domain_key or ordinal != expected_ordinal[kind]:
                raise CodeCompassDomainSupplementError("domain_supplement_chunk_ordinal_invalid")
            expected_ordinal[kind] += 1
            raw_bytes += len(raw)
            if raw_bytes > _MAX_SELECTED_RAW_BYTES:
                raise CodeCompassDomainSupplementError("domain_supplement_selected_budget_exceeded")
            records[kind].extend(
                self._parse_records(
                    raw=raw,
                    row_count=row_count,
                    payload_kind=kind,
                    domain_key=domain_key,
                )
            )
        expected_counts = {
            "nodes": summary.semantic_node_count,
            "semantic_edges": summary.semantic_edge_count,
            "declaration_edges": summary.declaration_edge_count,
        }
        if any(len(records[kind]) != expected for kind, expected in expected_counts.items()):
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_row_count_mismatch")
        expected_bytes = summary.semantic_node_bytes + summary.semantic_edge_bytes + summary.declaration_edge_bytes
        if raw_bytes != expected_bytes:
            raise CodeCompassDomainSupplementError("domain_supplement_domain_byte_count_mismatch")
        return (
            (
                tuple(records["nodes"]),
                tuple(records["semantic_edges"]),
                tuple(records["declaration_edges"]),
            ),
            raw_bytes,
        )

    @staticmethod
    def _assert_stream_counts(
        *,
        catalog: CodeCompassDomainSupplementCatalog,
        observed: Mapping[str, Mapping[str, Sequence[int]]],
    ) -> None:
        for summary in catalog.domains:
            expected = {
                "nodes": (
                    summary.semantic_node_count,
                    summary.semantic_node_bytes,
                ),
                "semantic_edges": (
                    summary.semantic_edge_count,
                    summary.semantic_edge_bytes,
                ),
                "declaration_edges": (
                    summary.declaration_edge_count,
                    summary.declaration_edge_bytes,
                ),
            }
            if any(
                (
                    int(observed[summary.domain_key][kind][1]) != count_and_bytes[0]
                    or int(observed[summary.domain_key][kind][2]) != count_and_bytes[1]
                )
                for kind, count_and_bytes in expected.items()
            ):
                raise CodeCompassDomainSupplementError("domain_supplement_chunk_row_count_mismatch")

    @staticmethod
    def _domain_logical_line(
        summary: CodeCompassDomainSupplementSummary,
    ) -> bytes:
        return (
            codecompass_domain_supplement_canonical_json_bytes(
                codecompass_domain_supplement_logical_domain(
                    {
                        "declaration_edge_bytes": (summary.declaration_edge_bytes),
                        "declaration_edge_count": (summary.declaration_edge_count),
                        "domain_key": summary.domain_key,
                        "domain_kind": summary.domain_kind,
                        "domain_label": summary.domain_label,
                        "semantic_edge_bytes": summary.semantic_edge_bytes,
                        "semantic_edge_count": summary.semantic_edge_count,
                        "semantic_node_bytes": summary.semantic_node_bytes,
                        "semantic_node_count": summary.semantic_node_count,
                        "source_file_count": summary.source_file_count,
                    }
                )
            )
            + b"\n"
        )

    @staticmethod
    def _chunk_logical_line(row: sqlite3.Row) -> bytes:
        return (
            codecompass_domain_supplement_canonical_json_bytes(
                codecompass_domain_supplement_logical_chunk_header(
                    chunk_ordinal=int(row["chunk_ordinal"]),
                    domain_key=str(row["domain_key"]),
                    payload_kind=str(row["payload_kind"]),
                    raw_sha256=str(row["raw_sha256"]),
                    raw_size=int(row["raw_size"]),
                    row_count=int(row["row_count"]),
                )
            )
            + b"\n"
        )

    def _cached_domain(
        self,
        *,
        binding: CodeCompassDomainSupplementBinding,
        domain_key: str,
    ) -> _CachedDomain | None:
        key = (
            binding.artifact_sha256,
            binding.logical_content_hash,
            domain_key,
        )
        with self._cache_lock:
            cached = self._domain_cache.pop(key, None)
            if cached is not None:
                self._domain_cache[key] = cached
            return cached

    def _cache_domain(
        self,
        *,
        binding: CodeCompassDomainSupplementBinding,
        domain_key: str,
        records: tuple[
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
        ],
        raw_size: int,
    ) -> None:
        if raw_size > self._maximum_cached_bytes:
            return
        key = (
            binding.artifact_sha256,
            binding.logical_content_hash,
            domain_key,
        )
        with self._cache_lock:
            replaced = self._domain_cache.pop(key, None)
            if replaced is not None:
                self._cached_bytes -= replaced.raw_size
            cached = _CachedDomain(records=records, raw_size=raw_size)
            self._domain_cache[key] = cached
            self._cached_bytes += raw_size
            while (
                len(self._domain_cache) > self._maximum_cached_domains
                or self._cached_bytes > self._maximum_cached_bytes
            ):
                _evicted_key, evicted = self._domain_cache.popitem(last=False)
                self._cached_bytes -= evicted.raw_size


codecompass_domain_supplement_reader = SqliteCodeCompassDomainSupplementReader()


def get_codecompass_domain_supplement_reader() -> SqliteCodeCompassDomainSupplementReader:
    return codecompass_domain_supplement_reader


__all__ = [
    "CodeCompassDomainSupplementBinding",
    "CodeCompassDomainSupplementCatalog",
    "CodeCompassDomainSupplementError",
    "CodeCompassDomainSupplementPort",
    "CodeCompassDomainSupplementRecords",
    "CodeCompassDomainSupplementSummary",
    "DOMAIN_SUPPLEMENT_FILENAME",
    "DOMAIN_SUPPLEMENT_MEDIA_TYPE",
    "DOMAIN_SUPPLEMENT_SCHEMA",
    "SqliteCodeCompassDomainSupplementReader",
    "get_codecompass_domain_supplement_reader",
]
