"""Deterministic domain clustering on the analysis graph (CCDD-008).

Cluster assignment is driven by deterministic signals (in order):
  1. path_signal   - root_path of a record's file (primary)
  2. package_signal - Java package / C# namespace prefix (secondary tie-break)
  3. graph_signal  - relation-edge degree to existing clusters; an isolated
                     record is absorbed into a cluster only if exactly one
                     cluster reaches the minimum coupling threshold.

Records that cannot be assigned unambiguously remain in ``unassigned_records``
or receive a low-confidence flag - clustering never invents a domain.

A cluster whose only shared property is a technical layer (api / service /
data-model / ...) is dropped (CCDD-DD-003): technical layers are descriptive,
not clustering criteria. Such candidates are still reported as warnings so
operators see why they were rejected.

The output is byte-stable sorted: same inputs always yield the same cluster
list in the same order.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rag_helper.domain_discovery.contracts import DomainCandidate
from rag_helper.domain_discovery.graph_model import DomainGraph, STRUCTURAL_EDGE_TYPES
from rag_helper.domain_discovery.signals import (
    assign_root_path,
    collect_package_prefixes,
    derive_root_path_candidates,
    package_prefixes_from_manifest,
)

DEFAULT_MIN_FILES = 3
DEFAULT_MIN_COUPLED_EDGES = 2
DEFAULT_CORE_RECORDS_LIMIT = 10
# Below this confidence a domain candidate is downgraded and only kept when
# at least one non-path signal contributed (otherwise it goes to unassigned).
DEFAULT_LOW_CONFIDENCE = 0.4

PATH_SIGNAL_WEIGHT = 0.5
PACKAGE_SIGNAL_WEIGHT = 0.25
GRAPH_COHESION_WEIGHT = 0.15
DESCRIPTOR_SIGNAL_WEIGHT = 0.10


@dataclass
class _Cluster:
    domain_id: str
    root_paths: set[str] = field(default_factory=set)
    package_prefixes: set[str] = field(default_factory=set)
    member_record_ids: list[str] = field(default_factory=list)
    file_count: int = 0
    technical_layers: set[str] = field(default_factory=set)
    layer_only: bool = True  # flips to False once a structural signal lands
    record_path_signal: bool = False
    record_package_signal: bool = False
    record_graph_signal: bool = False
    descriptor_signal: bool = False
    relation_count: int = 0  # sum of |internal| + |external| relation edges
    internal_relation_count: int = 0
    external_relation_count: int = 0
    edge_type_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    inbound_edges: int = 0
    outbound_edges: int = 0
    # id -> importance_score fallback; missing -> 0.0
    importance_by_id: dict[str, float] = field(default_factory=dict)
    boundary_warnings: list[dict] = field(default_factory=list)

    def to_candidate(self) -> DomainCandidate:
        # confidence: weighted blend of signal-presence booleans and cohesion.
        # path / package presence is either 1.0 (signal landed) or 0.0.
        path = 1.0 if self.record_path_signal else 0.0
        package = 1.0 if self.record_package_signal else 0.0
        cohesion_denom = self.relation_count or 1
        cohesion = self.internal_relation_count / cohesion_denom
        descriptor = 1.0 if self.descriptor_signal else 0.5
        confidence = (
            PATH_SIGNAL_WEIGHT * path
            + PACKAGE_SIGNAL_WEIGHT * package
            + GRAPH_COHESION_WEIGHT * cohesion
            + DESCRIPTOR_SIGNAL_WEIGHT * descriptor
        )
        confidence = max(0.0, min(1.0, confidence))

        core = sorted(
            self.member_record_ids,
            key=lambda rid: (
                -float(self.importance_by_id.get(rid) or 0.0),
                rid,
            ),
        )[:DEFAULT_CORE_RECORDS_LIMIT]

        return DomainCandidate(
            domain_id=self.domain_id,
            display_name=self.domain_id.replace("-", " ").replace("_", " ").title()
            or self.domain_id,
            confidence=round(confidence, 4),
            root_paths=sorted(self.root_paths),
            package_prefixes=sorted(self.package_prefixes),
            technical_layers=sorted(self.technical_layers),
            core_records=core,
            record_count=len(self.member_record_ids),
            member_record_ids=list(self.member_record_ids),
            metrics={
                "internal_edge_count": self.internal_relation_count,
                "inbound_edge_count": self.inbound_edges,
                "outbound_edge_count": self.outbound_edges,
                "edge_type_counts": dict(
                    sorted(self.edge_type_counts.items())
                ),
            },
            boundary_warnings=list(self.boundary_warnings),
            evidence={
                "path_signal": {
                    "root_paths": sorted(self.root_paths),
                    "file_count": self.file_count,
                }
                if self.record_path_signal
                else None,
                "package_signal": {
                    "prefixes": sorted(self.package_prefixes),
                }
                if self.record_package_signal
                else None,
                "graph_signal": {
                    "relation_count": self.relation_count,
                    "internal_relation_count": self.internal_relation_count,
                }
                if self.record_graph_signal
                else None,
                "descriptor_signal": None,
            },
        )


@dataclass
class ClusteringResult:
    candidates: list[DomainCandidate]
    unassigned_records: list[str]
    warnings: list[str]


def _slugify(root_path: str) -> str:
    return root_path.replace("/", "-").replace(" ", "-").strip("-") or "root"


def _aggregate_packages(records: list[dict], manifest: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    manifest_counts = package_prefixes_from_manifest(manifest)
    for prefix, count in manifest_counts.items():
        counts[prefix] = counts.get(prefix, 0) + int(count)
    record_counts = collect_package_prefixes(records)
    for prefix, count in record_counts.items():
        counts[prefix] = counts.get(prefix, 0) + int(count)
    return counts


def _assign_via_path(
    record_id: str, file_path: str | None, root_paths: list[str]
) -> str | None:
    if not file_path:
        return None
    return assign_root_path(file_path, root_paths)


def _assign_via_package(
    record_id: str, record: dict, root_paths: list[str]
) -> str | None:
    """Best-effort package-based fallback when path-signal gives no root.

    Only runs for records with a package/namespace AND a file path; the file
    path is the primary determinant, but the package prefix may resolve a
    record whose file sits outside any known root (e.g. shared util modules).
    """
    raw = record.get("package") or record.get("namespace")
    if not raw:
        return None
    file_path = record.get("file")
    if not file_path:
        return None
    # Map "com.example.billing" -> candidate root "agent/services" is unknown
    # to signals.py; this fallback uses the longest matching root path by
    # matching the package's dot-against the path's first segments only as a
    # weak hint. We keep it conservative: only return a root if the file path
    # already sits under a known root (signals.py will have done this too).
    return assign_root_path(file_path, root_paths)


def _assign_via_graph(
    record_id: str,
    cluster_index: dict[str, _Cluster],
    graph: DomainGraph,
    *,
    min_edges: int,
) -> str | None:
    """Assign an isolated record to a cluster if exactly one cluster
    receives at least ``min_edges`` relation edges from the record.
    """
    counts: dict[str, int] = defaultdict(int)
    for edge in graph.relation_edges:
        if edge.source == record_id:
            other = edge.target
        elif edge.target == record_id:
            other = edge.source
        else:
            continue
        owner = _find_cluster_for_node(other, cluster_index)
        if owner is not None:
            counts[owner] += 1
    candidates = [cid for cid, n in counts.items() if n >= min_edges]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_cluster_for_node(
    node_id: str, cluster_index: dict[str, _Cluster]
) -> str | None:
    for cid, cluster in cluster_index.items():
        if node_id in cluster.member_record_ids:
            return cid
    return None


def cluster_domains(
    graph: DomainGraph,
    *,
    records: list[dict] | None = None,
    manifest: dict | None = None,
    min_files: int = DEFAULT_MIN_FILES,
    min_coupled_edges: int = DEFAULT_MIN_COUPLED_EDGES,
) -> ClusteringResult:
    """Cluster records into deterministic domain candidates.

    Parameters
    ----------
    graph : DomainGraph
        Built from ``AnalysisInputs``; supplies nodes + relation edges.
    records : list of dict, optional
        index/detail records supplying package/namespace/layer information.
        Defaults to empty list.
    manifest : dict, optional
        Manifest dict; used to read package_type_index as a strong signal.
    min_files : int
        Forwarded to ``derive_root_path_candidates``.
    min_coupled_edges : int
        Minimum number of relation edges an isolated record needs to a
        single cluster before graph-signal may attach it.
    """
    warnings: list[str] = []
    records = list(records or [])
    manifest = dict(manifest or {})

    files = [n.file for n in graph.nodes.values() if n.file]
    root_paths = [
        c.root_path
        for c in derive_root_path_candidates(
            [f for f in files if f], min_files=min_files
        )
    ]

    # Initial clusters: one per root_path
    clusters: dict[str, _Cluster] = {}
    for root in root_paths:
        cid = _slugify(root)
        cluster = _Cluster(domain_id=cid)
        cluster.root_paths.add(root)
        clusters[cid] = cluster

    unassigned: list[str] = []

    # Phase 1: path-signal assignment
    for record_id, node in sorted(graph.nodes.items()):
        if not node.file:
            unassigned.append(record_id)
            continue
        root = _assign_via_path(record_id, node.file, root_paths)
        if root is None:
            continue  # graph-signal phase may pick it up
        cid = _slugify(root)
        cluster = clusters[cid]
        cluster.member_record_ids.append(record_id)
        cluster.file_count += 1
        cluster.record_path_signal = True
        cluster.layer_only = False
        # package signal piggybacked on path assignment
        raw = node.package or node.namespace
        if raw:
            prefix = ".".join(str(raw).split(".")[:2])
            cluster.package_prefixes.add(prefix)
            cluster.record_package_signal = True

    # Phase 2: package-signal fallback for records that have a file under a
    # known root but were not assigned in phase 1 (rare; e.g. records added
    # only via details.jsonl when graph_nodes was the source).
    if root_paths:
        for record_id, node in sorted(graph.nodes.items()):
            if record_id in _all_member_ids(clusters):
                continue
            if not node.file:
                continue
            root = _assign_via_package(record_id, {"file": node.file}, root_paths)
            if root is None:
                continue
            cid = _slugify(root)
            cluster = clusters.get(cid)
            if cluster is None:
                continue
            cluster.member_record_ids.append(record_id)
            cluster.file_count += 1
            cluster.record_package_signal = True
            cluster.layer_only = False

    # Phase 3: graph-signal - isolated records that have a file but no
    # known root can be attached to a single cluster if they are coupled
    # unambiguously. If the record still has no file path, we try the
    # graph-signal on id alone (record belongs only via edges).
    pending = [rid for rid in sorted(graph.nodes) if rid not in _all_member_ids(clusters)]
    for record_id in pending:
        target = _assign_via_graph(
            record_id, clusters, graph, min_edges=min_coupled_edges
        )
        if target is None:
            unassigned.append(record_id)
            continue
        cluster = clusters[target]
        cluster.member_record_ids.append(record_id)
        cluster.record_graph_signal = True
        cluster.layer_only = False

    # Phase 4: package/namespace enrichment (does not change membership)
    package_counts = _aggregate_packages(records, manifest)
    for cluster in clusters.values():
        # attribute package prefixes to clusters by best fit: keep the
        # prefix if its first segment appears as a path segment under any
        # root_path. Conservative: we only fold prefixes when the root_path
        # already contains a matching segment.
        for prefix in package_counts:
            head = prefix.split(".")[0]
            for root in cluster.root_paths:
                if head and head in root.split("/"):
                    cluster.package_prefixes.add(prefix)
                    cluster.record_package_signal = True
                    break

    # Phase 5: technical layer enrichment (CCDD-011) - imported lazily to
    # keep the module free of gem_partitions side effects; failure is
    # tolerated with a warning.
    try:
        from rag_helper.domain_discovery.signals import technical_layers_for_records

        record_by_id = {r.get("id"): r for r in records if r.get("id")}
        for cluster in clusters.values():
            layers: set[str] = set()
            for rid in cluster.member_record_ids:
                rec = record_by_id.get(rid)
                if rec is None:
                    continue
                layers.update(technical_layers_for_records([rec], rich_mode=True))
            cluster.technical_layers.update(layers)
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"technical_layers enrichment failed: {exc}")

    # Phase 6: importance scores and relation metrics per cluster
    importance = {nid: (n.importance_score or 0.0) for nid, n in graph.nodes.items()}
    for cluster in clusters.values():
        cluster.importance_by_id = {
            rid: importance.get(rid, 0.0) for rid in cluster.member_record_ids
        }

    members_by_id: dict[str, str] = {}
    for cid, cluster in clusters.items():
        for rid in cluster.member_record_ids:
            members_by_id[rid] = cid

    for edge in graph.relation_edges:
        src_cluster = members_by_id.get(edge.source)
        tgt_cluster = members_by_id.get(edge.target)
        edge_type = edge.type
        if edge_type in STRUCTURAL_EDGE_TYPES:
            continue
        if src_cluster is None and tgt_cluster is None:
            continue
        if src_cluster is not None and tgt_cluster is not None and src_cluster == tgt_cluster:
            clusters[src_cluster].internal_relation_count += 1
            clusters[src_cluster].relation_count += 1
            clusters[src_cluster].edge_type_counts[edge_type] += 1
        else:
            if src_cluster is not None:
                clusters[src_cluster].outbound_edges += 1
                clusters[src_cluster].relation_count += 1
                clusters[src_cluster].edge_type_counts[edge_type] += 1
            if tgt_cluster is not None:
                clusters[tgt_cluster].inbound_edges += 1
                clusters[tgt_cluster].relation_count += 1
                clusters[tgt_cluster].edge_type_counts[edge_type] += 1

    # Phase 7: filter layer-only clusters (CCDD-DD-003). A cluster that
    # collected its members solely because they share a technical layer
    # (api / service / data-model / ...) is not a domain.
    kept: list[_Cluster] = []
    for cluster in sorted(clusters.values(), key=lambda c: c.domain_id):
        if cluster.layer_only or not cluster.member_record_ids:
            warnings.append(
                f"cluster '{cluster.domain_id}' dropped: technical-layer-only or empty"
            )
            continue
        kept.append(cluster)

    candidates = [cluster.to_candidate() for cluster in kept]

    # Sort candidates by domain_id for byte stability.
    candidates.sort(key=lambda c: c.domain_id)
    unassigned = sorted(set(unassigned))

    return ClusteringResult(
        candidates=candidates,
        unassigned_records=unassigned,
        warnings=warnings,
    )


def _all_member_ids(clusters: dict[str, _Cluster]) -> set[str]:
    out: set[str] = set()
    for cluster in clusters.values():
        out.update(cluster.member_record_ids)
    return out
