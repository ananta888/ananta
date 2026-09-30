"""Shared constants, value types and SQLite helpers of the domain supplement.

Both the raw source writer and the published-artifact materializer depend on
these primitives; keeping them here lets each collaborator stay independent.
"""

from __future__ import annotations

import hashlib
import sqlite3
import urllib.parse
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from ananta_contracts.codecompass_domain_supplement import (
    codecompass_domain_supplement_canonical_json_bytes,
    codecompass_domain_supplement_encode_metadata,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
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
