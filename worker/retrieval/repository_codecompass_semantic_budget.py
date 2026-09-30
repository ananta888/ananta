"""Budgeted collectors for semantic graph records of the repository bridge.

The collector keeps one endpoint-closed partition within record and byte
budgets; the spool keeps a deterministic, order-independent reservoir of
deferred edges.  Both are pure in-memory value holders without I/O.
"""

from __future__ import annotations

import hashlib
import heapq
import json
from collections.abc import Iterator, Mapping
from typing import Any

from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    MAX_CODECOMPASS_SEMANTIC_PARTITIONS,
)
from ananta_contracts.codecompass_semantic_partitions import (
    CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


_DEFERRED_EDGE_DOMAIN_FIELD = "__ananta_semantic_partition_domain"


class _BoundedSemanticGraphCollector:
    """Collect endpoint-closed semantic partitions within explicit budgets."""

    def __init__(
        self,
        *,
        max_records_per_partition: int,
        max_bytes_per_partition: int,
    ) -> None:
        self._limit = max(1, int(max_records_per_partition))
        self._max_bytes = max(1, int(max_bytes_per_partition))
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.node_bytes = 0
        self.edge_bytes = 0
        self.truncated_node_count = 0
        self.truncated_edge_count = 0
        self.unresolved_edge_count = 0

    def add_node(self, node: dict[str, Any]) -> str | None:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            return None
        if node_id in self.nodes:
            return node_id
        record_bytes = self._record_bytes(node)
        if len(self.nodes) >= self._limit or self.node_bytes + record_bytes > self._max_bytes:
            self.truncated_node_count += 1
            return None
        self.nodes[node_id] = node
        self.node_bytes += record_bytes
        return node_id

    def add_edge(
        self,
        edge: dict[str, Any],
        *,
        additional_node_ids: set[str],
    ) -> bool:
        source = str(edge.get("source") or edge.get("source_id") or "").strip()
        target = str(edge.get("target") or edge.get("target_id") or "").strip()
        if not source or not target:
            self.unresolved_edge_count += 1
            return False
        identity = _canonical_json(edge)
        if identity in self.edges:
            return True
        source_available = source in self.nodes or source in additional_node_ids
        target_available = target in self.nodes or target in additional_node_ids
        if not source_available or not target_available:
            self.unresolved_edge_count += 1
            return False
        record_bytes = self._record_bytes(edge)
        if len(self.edges) >= self._limit or self.edge_bytes + record_bytes > self._max_bytes:
            self.truncated_edge_count += 1
            return False
        self.edges[identity] = edge
        self.edge_bytes += record_bytes
        return True

    def can_resolve_edge(
        self,
        edge: Mapping[str, Any],
        *,
        additional_node_ids: set[str],
    ) -> bool:
        source = str(edge.get("source") or edge.get("source_id") or "").strip()
        target = str(edge.get("target") or edge.get("target_id") or "").strip()
        return bool(
            source
            and target
            and (source in self.nodes or source in additional_node_ids)
            and (target in self.nodes or target in additional_node_ids)
        )

    @staticmethod
    def _record_bytes(record: Mapping[str, Any]) -> int:
        return len((_canonical_json(record) + "\n").encode("utf-8"))

    @property
    def truncated(self) -> bool:
        return bool(self.truncated_node_count or self.truncated_edge_count)


class _BoundedSemanticEdgeSpool:
    """Keep a deterministic, order-independent reservoir of deferred edges."""

    def __init__(
        self,
        *,
        max_records: int,
        max_bytes: int,
        max_tracked_candidates: int = MAX_CODECOMPASS_SEMANTIC_EDGE_CANDIDATES,
    ) -> None:
        self.max_records = max(1, int(max_records))
        self.max_bytes = max(1, int(max_bytes))
        # Upper bound of distinct candidate identities remembered for
        # duplicate suppression and loss attribution.
        self._max_tracked_candidates = int(max_tracked_candidates)
        # Fixed slots make retention depend only on canonical hash priority,
        # never on arrival order or on a previously evicted variable-size row.
        self.max_record_bytes = max(1, self.max_bytes // self.max_records)
        self._seen_candidate_hashes: set[bytes] = set()
        self._tracked_candidate_count_by_domain: dict[str | None, int] = {}
        self._records: dict[str, tuple[int, int, str | None, bool]] = {}
        self._worst_first: list[tuple[int, str]] = []
        self._byte_count = 0
        self._saturated_lost_domains: set[str] = set()
        self._saturated_unattributed_loss = False
        self._saturated_domain_tracking_overflow = False

    def append(self, edge: Mapping[str, Any]) -> None:
        serialized = _canonical_json(edge)
        if serialized in self._records:
            return
        digest_record = dict(edge)
        digest_record.pop(CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD, None)
        digest = hashlib.sha256(_canonical_json(digest_record).encode("utf-8")).digest()
        if digest in self._seen_candidate_hashes:
            return
        domain = self._candidate_domain(edge)
        identity_tracked = False
        if len(self._seen_candidate_hashes) < self._max_tracked_candidates:
            self._seen_candidate_hashes.add(digest)
            self._tracked_candidate_count_by_domain[domain] = self._tracked_candidate_count_by_domain.get(domain, 0) + 1
            identity_tracked = True
        else:
            # Keep duplicate tracking bounded. Once saturated, the reported
            # truncation counts remain conservative lower bounds.
            identity_tracked = False
        serialized_bytes = len((serialized + "\n").encode("utf-8"))
        if serialized_bytes > self.max_record_bytes:
            if not identity_tracked:
                self._record_saturated_loss(domain)
            return

        priority = int.from_bytes(digest, byteorder="big")
        self._records[serialized] = (
            priority,
            serialized_bytes,
            domain,
            identity_tracked,
        )
        self._byte_count += serialized_bytes
        heapq.heappush(self._worst_first, (-priority, serialized))
        while len(self._records) > self.max_records:
            _negated_priority, evicted = heapq.heappop(self._worst_first)
            (
                _evicted_priority,
                evicted_bytes,
                evicted_domain,
                evicted_identity_tracked,
            ) = self._records.pop(evicted)
            self._byte_count -= evicted_bytes
            if not evicted_identity_tracked:
                self._record_saturated_loss(evicted_domain)

    def records(self) -> Iterator[dict[str, Any]]:
        ordered = sorted(
            self._records,
            key=lambda serialized: (self._records[serialized][0], serialized),
        )
        for serialized in ordered:
            parsed = json.loads(serialized)
            if isinstance(parsed, dict):
                yield parsed

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def byte_count(self) -> int:
        return self._byte_count

    @property
    def truncated_edge_count(self) -> int:
        """Return a bounded lower bound for distinct discarded candidates."""

        return sum(self.lost_by_domain.values()) + self.unattributed_truncated_edge_count

    @property
    def lost_by_domain(self) -> dict[str, int]:
        """Return discarded-candidate lower bounds for opaque partition owners."""

        retained_tracked_by_domain: dict[str | None, int] = {}
        for _priority, _record_bytes, domain, identity_tracked in self._records.values():
            if identity_tracked:
                retained_tracked_by_domain[domain] = retained_tracked_by_domain.get(domain, 0) + 1
        losses = {
            domain: tracked_count - retained_tracked_by_domain.get(domain, 0)
            for domain, tracked_count in self._tracked_candidate_count_by_domain.items()
            if domain is not None and tracked_count > retained_tracked_by_domain.get(domain, 0)
        }
        for domain in self._saturated_lost_domains:
            losses[domain] = losses.get(domain, 0) + 1
        return losses

    @property
    def unattributed_truncated_edge_count(self) -> int:
        retained_unattributed = sum(
            1
            for _priority, _record_bytes, domain, identity_tracked in self._records.values()
            if identity_tracked and domain is None
        )
        tracked_unattributed = self._tracked_candidate_count_by_domain.get(None, 0)
        saturated_lower_bound = int(self._saturated_unattributed_loss or self._saturated_domain_tracking_overflow)
        return max(0, tracked_unattributed - retained_unattributed) + saturated_lower_bound

    @staticmethod
    def _candidate_domain(edge: Mapping[str, Any]) -> str | None:
        raw_domain = edge.get(_DEFERRED_EDGE_DOMAIN_FIELD)
        domain = str(raw_domain or "")
        return domain or None

    def _record_saturated_loss(self, domain: str | None) -> None:
        if domain is None:
            self._saturated_unattributed_loss = True
            return
        if domain in self._saturated_lost_domains:
            return
        if len(self._saturated_lost_domains) < MAX_CODECOMPASS_SEMANTIC_PARTITIONS:
            self._saturated_lost_domains.add(domain)
            return
        self._saturated_domain_tracking_overflow = True

    def __enter__(self) -> _BoundedSemanticEdgeSpool:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


__all__ = [
    "_BoundedSemanticEdgeSpool",
    "_BoundedSemanticGraphCollector",
    "_DEFERRED_EDGE_DOMAIN_FIELD",
    "_canonical_json",
]
