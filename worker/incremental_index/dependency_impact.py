"""
CodeCompass Incremental Index - Dependency Impact Analysis

Analysiert Abhängigkeiten zwischen Artefakten und berechnet den Impact von Änderungen.
Unterstützt Symbol-Graph-basierte Dependency-Resolution für inkrementelle Updates.
"""

import json
import hashlib
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DependencyNode:
    """Ein Knoten im Dependency-Graph."""
    artifact_id: str
    artifact_type: str  # symbol_graph, semantic_chunks, embeddings, fts_index, etc.
    file_path: str
    symbols: List[str] = field(default_factory=list)
    dependencies: Set[str] = field(default_factory=set)  # IDs von abhängigen Nodes
    dependents: Set[str] = field(default_factory=set)  # IDs von Nodes die davon abhängen
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImpactResult:
    """Ergebnis der Impact-Analyse für ein ChangeSet."""
    changeset_id: str
    direct_impact: Set[str]  # Direkt betroffene Artifact-IDs
    transitive_impact: Set[str]  # Transitiv betroffene Artifact-IDs
    impact_graph: Dict[str, List[str]]  # Adjazenzliste des Impact-Subgraphs
    severity_score: float  # 0.0 - 1.0, basierend auf Impact-Breite
    recommended_action: str  # "rebuild_all", "rebuild_affected", "skip"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "direct_impact": sorted(list(self.direct_impact)),
            "transitive_impact": sorted(list(self.transitive_impact)),
            "impact_graph": {k: sorted(v) for k, v in self.impact_graph.items()},
            "severity_score": round(self.severity_score, 3),
            "recommended_action": self.recommended_action,
            "total_affected": len(self.direct_impact) + len(self.transitive_impact)
        }


class DependencyImpactAnalyzer:
    """
    Analysiert den Impact von Dateiänderungen auf Artefakte mittels Dependency-Graph.
    
    Features:
    - Symbol-basierte Dependency-Resolution aus existing symbol_graph
    - Transitive Impact-Berechnung via Graph-Traversierung
    - Severity-Scoring für Build-Entscheidungen
    - Support für mehrere Artifact-Typen
    """
    
    def __init__(self, symbol_graph: Optional[Dict[str, Any]] = None):
        """
        Initialisiert den Analyzer mit einem optionalen Symbol-Graph.
        
        Args:
            symbol_graph: CodeCompass symbol_graph Manifest oder None
        """
        self.symbol_graph = symbol_graph or {}
        self.nodes: Dict[str, DependencyNode] = {}
        self._build_dependency_graph()
    
    def _build_dependency_graph(self):
        """Buildet den Dependency-Graph aus dem Symbol-Graph."""
        if not self.symbol_graph:
            return
        
        nodes_data = self.symbol_graph.get("nodes", [])
        edges_data = self.symbol_graph.get("edges", [])
        
        # Nodes erstellen
        for node in nodes_data:
            node_id = node.get("id", "")
            if not node_id:
                continue
            
            artifact_type = self._infer_artifact_type(node)
            file_path = node.get("path", node.get("file", ""))
            symbols = node.get("symbols", [])
            
            self.nodes[node_id] = DependencyNode(
                artifact_id=node_id,
                artifact_type=artifact_type,
                file_path=file_path,
                symbols=symbols,
                metadata=node
            )
        
        # Edges verarbeiten (Dependencies)
        for edge in edges_data:
            source_id = edge.get("source", "")
            target_id = edge.get("target", "")
            edge_type = edge.get("type", "")
            
            # Nur dependency-relevante Kanten berücksichtigen
            if edge_type in ["depends_on", "imports", "calls", "references"]:
                if source_id in self.nodes and target_id in self.nodes:
                    self.nodes[source_id].dependencies.add(target_id)
                    self.nodes[target_id].dependents.add(source_id)
    
    def _infer_artifact_type(self, node: Dict[str, Any]) -> str:
        """Leitet den Artifact-Typ aus Node-Metadaten ab."""
        node_type = node.get("type", "").lower()
        
        if "symbol" in node_type or "function" in node_type or "class" in node_type:
            return "symbol_graph"
        elif "chunk" in node_type or "segment" in node_type:
            return "semantic_chunks"
        elif "embedding" in node_type:
            return "embeddings"
        elif "index" in node_type or "term" in node_type:
            return "fts_index"
        else:
            return "unknown"
    
    def analyze_impact(
        self, 
        changed_files: List[str], 
        changeset_id: str
    ) -> ImpactResult:
        """
        Analysiert den Impact von geänderten Dateien.
        
        Args:
            changed_files: Liste der geänderten Dateipfade
            changeset_id: ID des ChangeSets
            
        Returns:
            ImpactResult mit direktem und transitivem Impact
        """
        direct_impact: Set[str] = set()
        
        # 1. Direkt betroffene Nodes finden (file-level matching)
        for file_path in changed_files:
            for node_id, node in self.nodes.items():
                if node.file_path == file_path or file_path.startswith(node.file_path + "/"):
                    direct_impact.add(node_id)
        
        # 2. Transitiven Impact berechnen (alle Dependents rekursiv)
        transitive_impact: Set[str] = set()
        visited: Set[str] = set()
        
        def collect_dependents(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)
            
            if node_id not in direct_impact:
                transitive_impact.add(node_id)
            
            for dependent_id in self.nodes.get(node_id, DependencyNode("", "", "")).dependents:
                collect_dependents(dependent_id)
        
        for node_id in direct_impact:
            collect_dependents(node_id)
        
        # 3. Impact-Graph konstruieren (nur betroffene Nodes)
        all_affected = direct_impact | transitive_impact
        impact_graph: Dict[str, List[str]] = {}
        
        for node_id in all_affected:
            node = self.nodes.get(node_id)
            if node:
                impacted_deps = [d for d in node.dependencies if d in all_affected]
                impact_graph[node_id] = impacted_deps
        
        # 4. Severity-Score berechnen
        total_nodes = len(self.nodes)
        affected_count = len(all_affected)
        severity_score = affected_count / total_nodes if total_nodes > 0 else 0.0
        
        # 5. Empfohlene Aktion bestimmen
        if severity_score > 0.5:
            recommended_action = "rebuild_all"
        elif severity_score > 0.0:
            recommended_action = "rebuild_affected"
        else:
            recommended_action = "skip"
        
        return ImpactResult(
            changeset_id=changeset_id,
            direct_impact=direct_impact,
            transitive_impact=transitive_impact,
            impact_graph=impact_graph,
            severity_score=severity_score,
            recommended_action=recommended_action
        )
    
    def get_rebuild_plan(self, impact_result: ImpactResult) -> List[str]:
        """
        Erstellt einen sortierten Rebuild-Plan basierend auf dem Impact.
        
        Args:
            impact_result: Ergebnis der Impact-Analyse
            
        Returns:
            Sortierte Liste von Artifact-IDs in Build-Reihenfolge (Topological Sort)
        """
        if impact_result.recommended_action == "skip":
            return []
        
        if impact_result.recommended_action == "rebuild_all":
            return sorted(self.nodes.keys())
        
        # Topological Sort für betroffene Nodes
        all_affected = impact_result.direct_impact | impact_result.transitive_impact
        in_degree: Dict[str, int] = {node_id: 0 for node_id in all_affected}
        
        # In-Degree berechnen
        for node_id in all_affected:
            for dep_id in self.nodes.get(node_id, DependencyNode("", "", "")).dependencies:
                if dep_id in all_affected:
                    in_degree[node_id] = in_degree.get(node_id, 0) + 1
        
        # Kahn's Algorithm
        queue = [node_id for node_id, degree in in_degree.items() if degree == 0]
        rebuild_order: List[str] = []
        
        while queue:
            queue.sort()  # Deterministische Reihenfolge
            node_id = queue.pop(0)
            rebuild_order.append(node_id)
            
            for dependent_id in self.nodes.get(node_id, DependencyNode("", "", "")).dependents:
                if dependent_id in in_degree:
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        queue.append(dependent_id)
        
        return rebuild_order
    
    def export_impact_report(self, impact_result: ImpactResult, output_path: str):
        """
        Exportiert einen Impact-Report als JSON.
        
        Args:
            impact_result: Ergebnis der Impact-Analyse
            output_path: Pfad zur Ausgabedatei
        """
        report = {
            "report_type": "dependency_impact_analysis",
            "version": "1.0",
            "result": impact_result.to_dict(),
            "rebuild_plan": self.get_rebuild_plan(impact_result),
            "graph_stats": {
                "total_nodes": len(self.nodes),
                "total_edges": sum(len(n.dependencies) for n in self.nodes.values()),
                "affected_nodes": len(impact_result.direct_impact) + len(impact_result.transitive_impact),
                "direct_changes": len(impact_result.direct_impact)
            }
        }
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)


def analyze_snapshot_impact(
    old_symbol_graph: Dict[str, Any],
    new_symbol_graph: Dict[str, Any],
    changeset_id: str
) -> ImpactResult:
    """
    Convenience-Funktion für Impact-Analyse zwischen zwei Snapshots.
    
    Args:
        old_symbol_graph: Symbol-Graph des alten Snapshots
        new_symbol_graph: Symbol-Graph des neuen Snapshots
        changeset_id: ID des ChangeSets
        
    Returns:
        ImpactResult mit Analyse-Ergebnissen
    """
    # Changed Files extrahieren
    old_files = {node.get("path", "") for node in old_symbol_graph.get("nodes", [])}
    new_files = {node.get("path", "") for node in new_symbol_graph.get("nodes", [])}
    
    added_files = new_files - old_files
    deleted_files = old_files - new_files
    common_files = old_files & new_files
    
    # Modified Files finden (SHA256 Vergleich)
    old_sha_map = {node.get("path", ""): node.get("sha256", "") for node in old_symbol_graph.get("nodes", [])}
    new_sha_map = {node.get("path", ""): node.get("sha256", "") for node in new_symbol_graph.get("nodes", [])}
    
    modified_files = {
        path for path in common_files 
        if old_sha_map.get(path, "") != new_sha_map.get(path, "")
    }
    
    changed_files = list(added_files | deleted_files | modified_files)
    
    # Impact-Analyse mit neuem Symbol-Graph
    analyzer = DependencyImpactAnalyzer(new_symbol_graph)
    return analyzer.analyze_impact(changed_files, changeset_id)


if __name__ == "__main__":
    # Beispiel-Nutzung
    sample_graph = {
        "nodes": [
            {"id": "file_main.py", "type": "file", "path": "src/main.py", "sha256": "abc123"},
            {"id": "func_hello", "type": "function", "path": "src/main.py", "symbols": ["hello"]},
            {"id": "file_utils.py", "type": "file", "path": "src/utils.py", "sha256": "def456"},
            {"id": "func_helper", "type": "function", "path": "src/utils.py", "symbols": ["helper"]}
        ],
        "edges": [
            {"source": "func_hello", "target": "func_helper", "type": "calls"}
        ]
    }
    
    analyzer = DependencyImpactAnalyzer(sample_graph)
    result = analyzer.analyze_impact(["src/utils.py"], "cs_001")
    
    print(f"Impact Result: {json.dumps(result.to_dict(), indent=2)}")
    print(f"Rebuild Plan: {analyzer.get_rebuild_plan(result)}")
