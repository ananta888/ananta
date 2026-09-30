"""Top-level domain partitioning and admission evidence for semantic shards."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
    MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
    MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
    codecompass_semantic_repository_root_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import SemanticDomainIdentity
from worker.retrieval.repository_codecompass_semantic_budget import (
    _BoundedSemanticGraphCollector,
)

_DOMAIN_ADMISSION_STRATEGY = "top_level_domain_bounded_admission_v1"


@dataclass(frozen=True)
class _SemanticDomainEvidence:
    identity: SemanticDomainIdentity
    status: str
    source_file_count: int
    semantic_file_count: int
    semantic_node_count: int
    semantic_edge_count: int
    semantic_node_bytes: int
    semantic_edge_bytes: int
    graph_declaration_count: int
    graph_declaration_bytes: int
    truncated_graph_declaration_count: int
    truncated_node_count: int
    truncated_edge_count: int
    unresolved_edge_count: int

    @property
    def domain_key(self) -> str:
        return self.identity.domain_key

    def to_wire(self) -> dict[str, object]:
        return {
            "domain_key": self.domain_key,
            "status": self.status,
            "source_file_count": self.source_file_count,
            "semantic_file_count": self.semantic_file_count,
            "semantic_node_count": self.semantic_node_count,
            "semantic_edge_count": self.semantic_edge_count,
            "semantic_node_bytes": self.semantic_node_bytes,
            "semantic_edge_bytes": self.semantic_edge_bytes,
            "graph_declaration_count": self.graph_declaration_count,
            "graph_declaration_bytes": self.graph_declaration_bytes,
            "truncated_graph_declaration_count": (self.truncated_graph_declaration_count),
            "truncated_node_count": self.truncated_node_count,
            "truncated_edge_count": self.truncated_edge_count,
            "unresolved_edge_count": self.unresolved_edge_count,
        }


@dataclass(frozen=True)
class _DomainAdmissionEvidence:
    domain_count: int
    materialized_domain_count: int
    omitted_domain_count: int
    empty_domain_count: int
    partition_count: int
    domains: Sequence[_SemanticDomainEvidence]

    _MAX_EVIDENCE = MAX_CODECOMPASS_SEMANTIC_PARTITIONS

    @staticmethod
    def _evidence_order(item: _SemanticDomainEvidence) -> tuple[int, str]:
        status_priority = {
            "materialized": 0,
            "aggregate_byte_limit": 1,
            "partition_limit": 1,
            "per_partition_limit": 1,
            "no_semantic_records": 2,
        }
        return status_priority[item.status], item.domain_key

    def to_wire(self) -> dict[str, object]:
        bounded_domains = tuple(sorted(self.domains, key=self._evidence_order)[: self._MAX_EVIDENCE])
        return {
            "strategy": _DOMAIN_ADMISSION_STRATEGY,
            "top_level_domain_count": self.domain_count,
            "materialized_domain_count": self.materialized_domain_count,
            "omitted_domain_count": self.omitted_domain_count,
            "empty_domain_count": self.empty_domain_count,
            "partition_count": self.partition_count,
            "evidence_count": len(bounded_domains),
            "evidence_truncated_count": (self.domain_count - len(bounded_domains)),
            "max_partitions": MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
            "max_total_bytes": MAX_CODECOMPASS_SEMANTIC_TOTAL_OUTPUT_BYTES,
            "aggregate_scope": "semantic_and_declaration_jsonl",
            "graph_declaration_bytes": sum(
                item.graph_declaration_bytes for item in self.domains if item.status == "materialized"
            ),
            "final_graph_artifact_max_bytes": (MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES),
            "final_materializer_fail_closed": True,
            "domains": [item.to_wire() for item in bounded_domains],
        }


class _TopLevelDomainPartitions:
    """Create deterministic, independently bounded top-level path shards.

    Smaller domains are evaluated first for the aggregate output envelope, but
    every admitted domain owns a separate collector.  Consequently a large
    lexically early domain can never consume another domain's 5,000-record / 4
    MiB shard allowance. Admission is intentionally greedy by ascending file
    count and opaque domain hash; the aggregate envelope and partition-count
    ceiling remain explicit safety limits for the final 32 MiB graph artifact.
    """

    def __init__(
        self,
        records: Sequence[tuple[str, dict[str, Any]]],
    ) -> None:
        grouped: dict[SemanticDomainIdentity, list[tuple[str, dict[str, Any]]]] = {}
        for path, record in records:
            grouped.setdefault(self._domain(path), []).append((path, record))
        groups = {domain: tuple(values) for domain, values in grouped.items()}
        self._groups = tuple(
            (identity, groups[identity])
            for identity in sorted(
                groups,
                key=lambda candidate: (
                    len(groups[candidate]),
                    candidate.domain_key,
                ),
            )
        )

    @staticmethod
    def _domain(path: str) -> SemanticDomainIdentity:
        head, separator, _tail = str(path or "").partition("/")
        if separator and head:
            return SemanticDomainIdentity(
                domain_key=codecompass_semantic_domain_key(head),
                domain_kind="top_level_path",
                domain_label=head,
            )
        return SemanticDomainIdentity(
            domain_key=codecompass_semantic_repository_root_domain_key(),
            domain_kind="repository_root",
            domain_label="",
        )

    @property
    def domain_count(self) -> int:
        return len(self._groups)

    def groups(
        self,
    ) -> Iterator[
        tuple[SemanticDomainIdentity, tuple[tuple[str, dict[str, Any]], ...]]
    ]:
        yield from self._groups


@dataclass
class _AcceptedSemanticDomain:
    identity: SemanticDomainIdentity
    collector: _BoundedSemanticGraphCollector
    source_file_count: int
    semantic_file_count: int
    graph_declaration_count: int
    graph_declaration_bytes: int
    truncated_graph_declaration_count: int


__all__ = [
    "_AcceptedSemanticDomain",
    "_DomainAdmissionEvidence",
    "_SemanticDomainEvidence",
    "_TopLevelDomainPartitions",
]
