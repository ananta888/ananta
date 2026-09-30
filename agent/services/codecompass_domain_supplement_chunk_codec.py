"""Chunk decoding, canonical JSONL parsing and logical hashing lines."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zlib
from collections.abc import Iterator, Mapping, Sequence

from agent.services.codecompass_domain_supplement_models import (
    DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN,
    DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET,
    MAX_DOMAIN_SUPPLEMENT_COMPRESSED_CHUNK_BYTES,
    MAX_DOMAIN_SUPPLEMENT_IDENTIFIER_CHARACTERS,
    CodeCompassDomainSupplementError,
    CodeCompassDomainSupplementSummary,
    domain_supplement_canonical_json,
)
from ananta_contracts.codecompass_domain_supplement import (
    codecompass_domain_supplement_canonical_json_bytes,
    codecompass_domain_supplement_logical_chunk_header,
    codecompass_domain_supplement_logical_domain,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)

_DOMAIN_KEY = DOMAIN_SUPPLEMENT_DOMAIN_KEY_PATTERN
_MAX_COMPRESSED_CHUNK_BYTES = MAX_DOMAIN_SUPPLEMENT_COMPRESSED_CHUNK_BYTES
_MAX_IDENTIFIER_CHARACTERS = MAX_DOMAIN_SUPPLEMENT_IDENTIFIER_CHARACTERS
_PAYLOAD_KINDS = DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET
_canonical_json = domain_supplement_canonical_json


def iter_domain_supplement_chunk_rows(
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


def decode_domain_supplement_chunk(
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


def parse_domain_supplement_records(
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


def domain_supplement_domain_logical_line(
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


def domain_supplement_chunk_logical_line(row: sqlite3.Row) -> bytes:
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


