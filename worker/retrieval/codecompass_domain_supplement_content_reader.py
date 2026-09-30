"""Canonical, budget-enforcing reader of domain supplement logical content.

The same logical stream is derived from the raw source store and from a
published supplement, so the content hash binds both representations.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from typing import Any

from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_LOGICAL_HASH_PREFIX,
    DOMAIN_SUPPLEMENT_PAYLOAD_KINDS,
    codecompass_domain_supplement_logical_chunk_header,
    codecompass_domain_supplement_logical_domain,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_CHUNK_BYTES,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS,
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
)
from worker.retrieval.codecompass_domain_supplement_primitives import (
    CodeCompassDomainSupplementExecutionDeadlinePort,
    DomainSupplementContent,
    _bounded_decompress,
    _canonical_json_bytes,
    _checkpoint,
)


class DomainSupplementLogicalContentReader:
    """Stream domain rows and payload chunks under explicit content budgets."""

    def __init__(
        self,
        *,
        max_domains: int = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_DOMAINS,
        max_raw_bytes: int = MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_RAW_BYTES,
    ) -> None:
        self._max_domains = int(max_domains)
        self._max_raw_bytes = int(max_raw_bytes)

    def logical_content(
        self,
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
        is_published = self._has_table(connection, "domain_payloads")
        for domain in self.domain_rows(
            connection,
            execution_deadline=execution_deadline,
        ):
            _checkpoint(execution_deadline)
            domain_count += 1
            if domain_count > self._max_domains:
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
                    chunks = self._published_chunks(
                        connection,
                        domain_key=str(domain["domain_key"]),
                        payload_kind=payload_kind,
                        execution_deadline=execution_deadline,
                    )
                else:
                    chunks = self.chunks(
                        self.records(
                            connection,
                            domain_key=str(domain["domain_key"]),
                            payload_kind=payload_kind,
                        )
                    )
                for ordinal, raw, row_count in chunks:
                    _checkpoint(execution_deadline)
                    raw_payload_bytes += len(raw)
                    if raw_payload_bytes > self._max_raw_bytes:
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
    def domain_rows(
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
        for record in cls.records(
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
    def records(
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
    def chunks(records: Iterator[bytes]) -> Iterator[tuple[int, bytes, int]]:
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


__all__ = ["DomainSupplementLogicalContentReader"]
