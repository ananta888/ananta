"""Schema, metadata and domain-summary validation for domain supplements."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping

from agent.services.codecompass_domain_supplement_models import (
    DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN,
    MAX_DOMAIN_SUPPLEMENT_IDENTIFIER_CHARACTERS,
    CodeCompassDomainSupplementBinding,
    CodeCompassDomainSupplementError,
    CodeCompassDomainSupplementSummary,
    domain_supplement_canonical_json,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_SCHEMA,
    DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID,
    DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION,
    codecompass_domain_supplement_decode_metadata,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)

_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_DOMAIN_KEY = DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN
_MAX_IDENTIFIER_CHARACTERS = MAX_DOMAIN_SUPPLEMENT_IDENTIFIER_CHARACTERS
_canonical_json = domain_supplement_canonical_json
_TABLES = frozenset({"supplement_meta", "domains", "domain_payloads"})
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


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").split())


def validate_domain_supplement_schema(connection: sqlite3.Connection) -> None:
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


def read_domain_supplement_metadata(connection: sqlite3.Connection) -> dict[str, object]:
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


def validate_domain_supplement_metadata(
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
        domain_supplement_metadata_count(metadata, key)


def domain_supplement_metadata_count(metadata: Mapping[str, object], key: str) -> int:
    raw = metadata.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise CodeCompassDomainSupplementError("domain_supplement_metadata_count_invalid")
    return raw


def parse_domain_supplement_summary(row: sqlite3.Row) -> CodeCompassDomainSupplementSummary:
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


