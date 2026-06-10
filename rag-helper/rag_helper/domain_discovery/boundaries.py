"""Boundary and coupling metrics for domain candidates (CCDD-009).

Consumes the output of ``cluster_domains`` and a ``DomainGraph`` and emits
per-domain ``external_domain_refs`` plus ``boundary_warnings``:

  - mutual_coupling: two domains have >= threshold relation edges in both
    directions - the boundary is suspect (likely a missing context split
    or a shared anti-corruption layer missing).
  - layer_spans_domains: a single technical layer (api / service / ...) is
    shared by >= 3 domains - signals a generic platform concern that
    should likely become its own component.
  - heterogeneous_root: a cluster accumulated mostly tiny, loosely coupled
    areas - the root path candidate is structurally weak.
  - descriptor_mismatch: (placeholder; populated by CCDD-010 when descriptor
    source_paths disagree with the discovered root paths).

The warning types are part of the documented contract
(``docs/codecompass-domain-discovery.md`` section 4) and must remain
byte-stable: same input => same warning list, sorted by
(source_domain, target_domain, warning_type).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from rag_helper.domain_discovery.clustering import ClusteringResult
from rag_helper.domain_discovery.contracts import DomainCandidate
from rag_helper.domain_discovery.graph_model import DomainGraph

DEFAULT_MUTUAL_COUPLING_THRESHOLD = 3
DEFAULT_LAYER_SPAN_DOMAINS = 3
DEFAULT_HETEROGENEOUS_MIN_RECORDS = 3
DEFAULT_HETEROGENEOUS_MAX_INTERNAL_EDGES = 1

WARNING_MUTUAL_COUPLING = "mutual_coupling"
WARNING_LAYER_SPANS_DOMAINS = "layer_spans_domains"
WARNING_HETEROGENEOUS_ROOT = "heterogeneous_root"
WARNING_DESCRIPTOR_MISMATCH = "descriptor_mismatch"


@dataclass
class _DirectionalPair:
    a_to_b: int = 0
    b_to_a: int = 0
    edge_type_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def total(self) -> int:
        return self.a_to_b + self.b_to_a


@dataclass
class BoundaryResult:
    candidates: list[DomainCandidate]
    boundary_warnings: list[dict]
    coupling_pairs: list[dict]
    layer_to_domains: dict[str, list[str]]
    warnings: list[str]


def _build_member_index(candidates: list[DomainCandidate]) -> dict[str, str]:
    index: dict[str, str] = {}
    for candidate in candidates:
        for rid in candidate.member_record_ids:
            index[rid] = candidate.domain_id
    return index


def _aggregate_pair_edges(
    graph: DomainGraph, members: dict[str, str]
) -> dict[tuple[str, str], _DirectionalPair]:
    pairs: dict[tuple[str, str], _DirectionalPair] = {}
    for edge in graph.relation_edges:
        src = members.get(edge.source)
        tgt = members.get(edge.target)
        if not src or not tgt or src == tgt:
            continue
        a, b = sorted((src, tgt))
        pair = pairs.setdefault((a, b), _DirectionalPair())
        # ``src`` and ``tgt`` are the domain names for this edge's actual
        # direction; ``(a, b)`` is the unordered key. Increment the
        # counter that matches the edge's real direction.
        if src == a:
            pair.a_to_b += 1
        else:
            pair.b_to_a += 1
        pair.edge_type_counts[edge.type] += 1
    return pairs


def _build_layer_index(
    candidates: list[DomainCandidate],
) -> dict[str, list[str]]:
    layers: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        for layer in candidate.technical_layers:
            layers[layer].append(candidate.domain_id)
    for layer, domains in layers.items():
        layers[layer] = sorted(set(domains))
    return dict(layers)


def _detect_mutual_coupling(
    pairs: dict[tuple[str, str], _DirectionalPair],
    *,
    threshold: int,
) -> list[dict]:
    warnings: list[dict] = []
    for (a, b), pair in pairs.items():
        if pair.a_to_b >= threshold and pair.b_to_a >= threshold:
            warnings.append(
                {
                    "source_domain": a,
                    "target_domain": b,
                    "warning_type": WARNING_MUTUAL_COUPLING,
                    "severity": "warning",
                    "evidence": {
                        "a_to_b_edges": pair.a_to_b,
                        "b_to_a_edges": pair.b_to_a,
                        "edge_type_counts": dict(
                            sorted(pair.edge_type_counts.items())
                        ),
                    },
                }
            )
    return warnings


def _detect_layer_spans(
    layer_index: dict[str, list[str]], *, min_domains: int
) -> list[dict]:
    warnings: list[dict] = []
    for layer, domains in sorted(layer_index.items()):
        if len(domains) >= min_domains:
            warnings.append(
                {
                    "source_domain": layer,
                    "target_domain": "*",
                    "warning_type": WARNING_LAYER_SPANS_DOMAINS,
                    "severity": "info",
                    "evidence": {"domains": list(domains), "count": len(domains)},
                }
            )
    return warnings


def _detect_heterogeneous_root(
    candidates: Iterable[DomainCandidate],
    *,
    min_records: int,
    max_internal_edges: int,
) -> list[dict]:
    warnings: list[dict] = []
    for candidate in candidates:
        internal = int(
            candidate.metrics.get("internal_edge_count", 0) or 0
        )
        if (
            candidate.record_count >= min_records
            and internal <= max_internal_edges
        ):
            warnings.append(
                {
                    "source_domain": candidate.domain_id,
                    "target_domain": "*",
                    "warning_type": WARNING_HETEROGENEOUS_ROOT,
                    "severity": "warning",
                    "evidence": {
                        "record_count": candidate.record_count,
                        "internal_edge_count": internal,
                        "root_paths": list(candidate.root_paths),
                    },
                }
            )
    return warnings


def _build_coupling_pairs(
    pairs: dict[tuple[str, str], _DirectionalPair],
) -> list[dict]:
    payload: list[dict] = []
    for (a, b), pair in sorted(pairs.items()):
        payload.append(
            {
                "source": a,
                "target": b,
                "edge_count": pair.total,
                "edge_type_counts": dict(sorted(pair.edge_type_counts.items())),
            }
        )
    return payload


def compute_external_domain_refs(
    candidates: list[DomainCandidate],
    pairs: dict[tuple[str, str], _DirectionalPair],
) -> None:
    """Mutate each candidate in-place: add ``external_domain_refs`` to metrics.

    external_domain_refs maps target_domain -> directed edge count
    (outbound or inbound, whichever touches this domain from outside).
    """
    for candidate in candidates:
        refs: dict[str, int] = {}
        for (a, b), pair in pairs.items():
            if candidate.domain_id == a:
                refs[b] = refs.get(b, 0) + pair.a_to_b
            elif candidate.domain_id == b:
                refs[a] = refs.get(a, 0) + pair.b_to_a
        if refs:
            candidate.metrics["external_domain_refs"] = dict(sorted(refs.items()))


def compute_boundary_metrics(
    clustering: ClusteringResult,
    graph: DomainGraph,
    *,
    mutual_threshold: int = DEFAULT_MUTUAL_COUPLING_THRESHOLD,
    layer_min_domains: int = DEFAULT_LAYER_SPAN_DOMAINS,
    heterogeneous_min_records: int = DEFAULT_HETEROGENEOUS_MIN_RECORDS,
    heterogeneous_max_internal_edges: int = (
        DEFAULT_HETEROGENEOUS_MAX_INTERNAL_EDGES
    ),
    descriptor_mismatches: list[dict] | None = None,
) -> BoundaryResult:
    """Compute coupling matrix and boundary warnings from a clustering result.

    ``descriptor_mismatches`` is supplied by CCDD-010 (descriptor ingestion);
    callers that do not yet read descriptors pass an empty list.
    """
    candidates = list(clustering.candidates)
    members = _build_member_index(candidates)
    pairs = _aggregate_pair_edges(graph, members)
    compute_external_domain_refs(candidates, pairs)

    layer_index = _build_layer_index(candidates)

    warnings: list[dict] = []
    warnings.extend(_detect_mutual_coupling(pairs, threshold=mutual_threshold))
    warnings.extend(_detect_layer_spans(layer_index, min_domains=layer_min_domains))
    warnings.extend(
        _detect_heterogeneous_root(
            candidates,
            min_records=heterogeneous_min_records,
            max_internal_edges=heterogeneous_max_internal_edges,
        )
    )
    if descriptor_mismatches:
        warnings.extend(descriptor_mismatches)

    # Sort warnings byte-stably so identical inputs yield identical output.
    warnings.sort(
        key=lambda w: (
            w.get("warning_type", ""),
            w.get("source_domain", ""),
            w.get("target_domain", ""),
        )
    )

    # Attach per-domain warnings to candidates.
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for warning in warnings:
        source = warning.get("source_domain", "")
        if source and source != "*" and any(
            c.domain_id == source for c in candidates
        ):
            by_domain[source].append(warning)
        # layer_spans / heterogeneous warnings carry "*" as target_domain
        # and are recorded against the source domain only.
    for candidate in candidates:
        candidate.boundary_warnings = by_domain.get(candidate.domain_id, [])

    coupling_pairs = _build_coupling_pairs(pairs)
    return BoundaryResult(
        candidates=candidates,
        boundary_warnings=warnings,
        coupling_pairs=coupling_pairs,
        layer_to_domains=layer_index,
        warnings=[],
    )
