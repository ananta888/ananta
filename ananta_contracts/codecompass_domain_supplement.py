"""Stable wire contract for revision-bound CodeCompass domain supplements.

Storage construction remains Worker-owned and lazy reads remain Hub-owned.
Only the byte-level interchange rules live here so both adapters calculate
the same identities without depending on each other's infrastructure code.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

DOMAIN_SUPPLEMENT_OUTPUT_ROLE: Final = "graph_domain_supplement"
DOMAIN_SUPPLEMENT_FILENAME: Final = "cc_graph_domains.sqlite3"
DOMAIN_SUPPLEMENT_SCHEMA: Final = "codecompass_graph_domain_supplement.v1"
DOMAIN_SUPPLEMENT_MEDIA_TYPE: Final = (
    "application/vnd.ananta.codecompass-domain-supplement+sqlite3"
)
DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID: Final = 0x414E4343  # ANCC
DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION: Final = 1
DOMAIN_SUPPLEMENT_PAYLOAD_KINDS: Final = (
    "declaration_edges",
    "nodes",
    "semantic_edges",
)
DOMAIN_SUPPLEMENT_DOMAIN_LOGICAL_FIELDS: Final = (
    "domain_key",
    "domain_kind",
    "domain_label",
    "source_file_count",
    "semantic_node_count",
    "semantic_edge_count",
    "declaration_edge_count",
    "semantic_node_bytes",
    "semantic_edge_bytes",
    "declaration_edge_bytes",
)
DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX: Final = (
    b"codecompass_graph_domain_supplement.logical.v1\n"
)


def codecompass_domain_supplement_canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def codecompass_domain_supplement_encode_metadata(value: object) -> str:
    """Encode a SQLite metadata value with one canonical representation."""

    return codecompass_domain_supplement_canonical_json_bytes(value).decode("utf-8")


def codecompass_domain_supplement_decode_metadata(value: object) -> object:
    """Decode a SQLite metadata value written by the canonical encoder."""

    return json.loads(str(value))


def codecompass_domain_supplement_logical_domain(
    values: Mapping[str, object],
) -> dict[str, object]:
    """Project exactly the stable domain fields covered by the logical hash."""

    return {
        field: values[field]
        for field in DOMAIN_SUPPLEMENT_DOMAIN_LOGICAL_FIELDS
    }


def codecompass_domain_supplement_logical_chunk_header(
    *,
    domain_key: str,
    payload_kind: str,
    chunk_ordinal: int,
    row_count: int,
    raw_size: int,
    raw_sha256: str,
) -> dict[str, object]:
    """Return the exact header hashed immediately before raw JSONL bytes."""

    if payload_kind not in DOMAIN_SUPPLEMENT_PAYLOAD_KINDS:
        raise ValueError("codecompass_domain_supplement_payload_kind_invalid")
    return {
        "chunk_ordinal": int(chunk_ordinal),
        "domain_key": str(domain_key),
        "payload_kind": payload_kind,
        "raw_sha256": str(raw_sha256),
        "raw_size": int(raw_size),
        "row_count": int(row_count),
    }


__all__ = [
    "DOMAIN_SUPPLEMENT_DOMAIN_LOGICAL_FIELDS",
    "DOMAIN_SUPPLEMENT_FILENAME",
    "DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX",
    "DOMAIN_SUPPLEMENT_MEDIA_TYPE",
    "DOMAIN_SUPPLEMENT_OUTPUT_ROLE",
    "DOMAIN_SUPPLEMENT_PAYLOAD_KINDS",
    "DOMAIN_SUPPLEMENT_SCHEMA",
    "DOMAIN_SUPPLEMENT_SQLITE_APPLICATION_ID",
    "DOMAIN_SUPPLEMENT_SQLITE_USER_VERSION",
    "codecompass_domain_supplement_canonical_json_bytes",
    "codecompass_domain_supplement_decode_metadata",
    "codecompass_domain_supplement_encode_metadata",
    "codecompass_domain_supplement_logical_chunk_header",
    "codecompass_domain_supplement_logical_domain",
]
