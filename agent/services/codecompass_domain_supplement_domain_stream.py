"""Per-domain record streaming and stream-count checks for domain supplements.

Loads the complete, ordinal-checked record stream of one supplement domain
within the selected raw-byte budget, and verifies observed chunk counters
against the catalog summaries after full-artifact validation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence

from agent.services.codecompass_domain_supplement_chunk_codec import (
    decode_domain_supplement_chunk,
    iter_domain_supplement_chunk_rows,
    parse_domain_supplement_records,
)
from agent.services.codecompass_domain_supplement_models import (
    DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET,
    CodeCompassDomainSupplementCatalog,
    CodeCompassDomainSupplementError,
    CodeCompassDomainSupplementSummary,
    invoke_domain_supplement_checkpoint,
)

DomainSupplementDomainRecords = tuple[
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
]


def load_domain_supplement_domain(
    *,
    connection: sqlite3.Connection,
    summary: CodeCompassDomainSupplementSummary,
    maximum_selected_raw_bytes: int,
    checkpoint: Callable[[], object] | None = None,
) -> tuple[DomainSupplementDomainRecords, int]:
    """Return ``((nodes, semantic_edges, declaration_edges), raw_bytes)``."""
    records: dict[str, list[Mapping[str, object]]] = {
        kind: [] for kind in DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET
    }
    expected_ordinal = {kind: 0 for kind in DOMAIN_SUPPLEMENT_PAYLOAD_KIND_SET}
    raw_bytes = 0
    for row in iter_domain_supplement_chunk_rows(
        connection,
        domain_keys=(summary.domain_key,),
    ):
        invoke_domain_supplement_checkpoint(checkpoint)
        domain_key, kind, ordinal, row_count, raw = decode_domain_supplement_chunk(row)
        if domain_key != summary.domain_key or ordinal != expected_ordinal[kind]:
            raise CodeCompassDomainSupplementError("domain_supplement_chunk_ordinal_invalid")
        expected_ordinal[kind] += 1
        raw_bytes += len(raw)
        if raw_bytes > maximum_selected_raw_bytes:
            raise CodeCompassDomainSupplementError("domain_supplement_selected_budget_exceeded")
        records[kind].extend(
            parse_domain_supplement_records(
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


def assert_domain_supplement_stream_counts(
    *,
    catalog: CodeCompassDomainSupplementCatalog,
    observed: Mapping[str, Mapping[str, Sequence[int]]],
) -> None:
    """Fail closed unless observed row/byte counters match every domain summary."""
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
