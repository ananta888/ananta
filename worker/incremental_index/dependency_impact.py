"""Impact analysis for incremental invalidation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DependencyNode:
    artifact_id: str
    artifact_type: str
    file_path: str
    symbols: list[str] = field(default_factory=list)
    dependencies: set[str] = field(default_factory=set)
    dependents: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImpactResult:
    changeset_id: str
    direct_impact: set[str]
    transitive_impact: set[str]
    impact_graph: dict[str, list[str]]
    severity_score: float
    recommended_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "direct_impact": sorted(self.direct_impact),
            "transitive_impact": sorted(self.transitive_impact),
            "impact_graph": {key: list(value) for key, value in self.impact_graph.items()},
            "severity_score": self.severity_score,
            "recommended_action": self.recommended_action,
        }


class DependencyImpactAnalyzer:
    def __init__(self, symbol_graph: dict[str, Any] | None = None) -> None:
        self.symbol_graph = dict(symbol_graph or {})
        self.nodes = self._build_dependency_graph()

    def _infer_artifact_type(self, node: dict[str, Any]) -> str:
        kind = str(node.get("kind") or node.get("type") or "file")
        if "symbol" in kind or "function" in kind or "class" in kind:
            return "symbol"
        return "file"

    def _build_dependency_graph(self) -> dict[str, DependencyNode]:
        nodes: dict[str, DependencyNode] = {}
        for raw in list(self.symbol_graph.get("nodes") or []):
            path = str(raw.get("path") or raw.get("file") or raw.get("id") or "")
            node_id = str(raw.get("id") or path)
            nodes[node_id] = DependencyNode(
                artifact_id=node_id,
                artifact_type=self._infer_artifact_type(raw),
                file_path=path,
                symbols=[str(raw.get("name") or raw.get("symbol") or "")],
            )
        for edge in list(self.symbol_graph.get("edges") or []):
            source = str(edge.get("source") or edge.get("from") or "")
            target = str(edge.get("target") or edge.get("to") or "")
            if source in nodes and target in nodes:
                nodes[source].dependencies.add(target)
                nodes[target].dependents.add(source)
        return nodes

    def analyze_impact(self, changed_files: list[str], changeset_id: str = "") -> ImpactResult:
        changed = {str(item) for item in changed_files}
        direct = {node.artifact_id for node in self.nodes.values() if node.file_path in changed or node.artifact_id in changed}
        if not self.nodes:
            direct = set(changed)
        transitive: set[str] = set()
        frontier = set(direct)
        while frontier:
            current = frontier.pop()
            node = self.nodes.get(current)
            if node is None:
                continue
            for dependent in node.dependents:
                if dependent not in direct and dependent not in transitive:
                    transitive.add(dependent)
                    frontier.add(dependent)
        total = max(1, len(self.nodes) or len(changed))
        severity = min(1.0, (len(direct) + 0.5 * len(transitive)) / total)
        action = "delta_build" if severity < 0.45 else "partial_base_rebuild"
        if not changed:
            action = "noop"
        graph = {item: sorted(self.nodes[item].dependents) for item in sorted(direct | transitive) if item in self.nodes}
        return ImpactResult(changeset_id, direct, transitive, graph, severity, action)

    def get_rebuild_plan(self, impact_result: ImpactResult) -> dict[str, Any]:
        return {
            "files": sorted(impact_result.direct_impact | impact_result.transitive_impact),
            "action": impact_result.recommended_action,
        }


def analyze_snapshot_impact(old_symbol_graph: dict[str, Any], new_symbol_graph: dict[str, Any], changeset_id: str) -> ImpactResult:
    old_paths = {str(item.get("path") or "") for item in list((old_symbol_graph or {}).get("nodes") or [])}
    new_paths = {str(item.get("path") or "") for item in list((new_symbol_graph or {}).get("nodes") or [])}
    changed = sorted((old_paths ^ new_paths) or ["sha256"])
    return DependencyImpactAnalyzer(new_symbol_graph).analyze_impact(changed, changeset_id)
