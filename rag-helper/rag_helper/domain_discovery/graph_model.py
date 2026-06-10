"""Internal analysis graph for domain discovery (CCDD-006).

Structural parent_child edges and relation edges are kept strictly apart:
structure is used for hierarchy only, relations drive coupling metrics.
graph_edges.jsonl already contains the relation records (build_graph_edges
merges them), so edges from relations.jsonl are deduplicated against it by
(source, target, type).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rag_helper.domain_discovery.inputs import AnalysisInputs

STRUCTURAL_EDGE_TYPES = {"parent_child"}


@dataclass
class GraphNode:
    id: str
    file: str
    kind: str
    role_labels: list[str] = field(default_factory=list)
    parent_id: str | None = None
    importance_score: float | None = None
    package: str | None = None
    namespace: str | None = None


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str
    confidence: float | None = None
    heuristic: bool | None = None


@dataclass
class DomainGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    structural_edges: list[GraphEdge] = field(default_factory=list)
    relation_edges: list[GraphEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, inputs: AnalysisInputs) -> "DomainGraph":
        graph = cls()
        graph._build_nodes(inputs)
        graph._build_edges(inputs)
        return graph

    def _build_nodes(self, inputs: AnalysisInputs) -> None:
        # graph_nodes.jsonl is the primary node source; index/detail records
        # supplement package/namespace fields not present in the graph export.
        package_by_id: dict[str, str] = {}
        namespace_by_id: dict[str, str] = {}
        for record in [*inputs.index_records, *inputs.detail_records]:
            record_id = record.get("id")
            if not record_id:
                continue
            if record.get("package"):
                package_by_id[record_id] = str(record["package"])
            if record.get("namespace"):
                namespace_by_id[record_id] = str(record["namespace"])

        node_sources = inputs.graph_nodes
        if not node_sources:
            # Fallback: derive nodes from index/detail records directly.
            node_sources = [
                {
                    "id": record.get("id"),
                    "kind": record.get("kind"),
                    "file": record.get("file"),
                    "parent_id": record.get("parent_id"),
                    "role_labels": record.get("role_labels"),
                    "importance_score": record.get("importance_score"),
                }
                for record in [*inputs.index_records, *inputs.detail_records]
                if record.get("id") and record.get("file")
            ]
            if node_sources:
                self.warnings.append(
                    "graph_nodes missing; derived nodes from index/detail records"
                )

        for node in node_sources:
            node_id = str(node.get("id"))
            if node_id in self.nodes:
                continue
            self.nodes[node_id] = GraphNode(
                id=node_id,
                file=str(node.get("file") or ""),
                kind=str(node.get("kind") or ""),
                role_labels=list(node.get("role_labels") or []),
                parent_id=node.get("parent_id"),
                importance_score=node.get("importance_score"),
                package=package_by_id.get(node_id),
                namespace=namespace_by_id.get(node_id),
            )

    def _build_edges(self, inputs: AnalysisInputs) -> None:
        seen: set[tuple[str, str, str]] = set()

        def add_edge(source: str, target: str, edge_type: str, confidence=None, heuristic=None) -> None:
            key = (source, target, edge_type)
            if key in seen:
                return
            seen.add(key)
            edge = GraphEdge(
                source=source,
                target=target,
                type=edge_type,
                confidence=confidence,
                heuristic=heuristic,
            )
            if edge_type in STRUCTURAL_EDGE_TYPES:
                self.structural_edges.append(edge)
            else:
                self.relation_edges.append(edge)

        for edge in inputs.graph_edges:
            add_edge(
                str(edge.get("source")),
                str(edge.get("target")),
                str(edge.get("type")),
                edge.get("confidence"),
                edge.get("heuristic"),
            )

        # relations.jsonl uses from/to/type; dedupe against graph_edges.
        for record in inputs.relation_records:
            source = record.get("from")
            target = record.get("to")
            edge_type = record.get("type") or record.get("relation")
            if not source or not target or not edge_type:
                continue
            add_edge(
                str(source),
                str(target),
                str(edge_type),
                record.get("confidence"),
                record.get("heuristic"),
            )

    def relation_degree(self, node_id: str) -> int:
        return sum(
            1
            for edge in self.relation_edges
            if edge.source == node_id or edge.target == node_id
        )
