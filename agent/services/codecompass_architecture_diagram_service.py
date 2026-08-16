"""HAC-011: Deterministische Diagramm-Generierung aus Architecture Slices.

Dieser Service erzeugt Mermaid/PlantUML/UML-Diagramme ausschließlich aus dem
bereits freigegebenen und validierten Architecture Slice. Es werden keine neuen
Beziehungen erfunden oder halluziniert.

Unterstützte Diagrammtypen:
- component: Komponentendiagramme mit Abhängigkeiten
- dependency: Dependency-Graphen zwischen Nodes
- system: System-/Container-Übersicht
- sequence: Sequenzdiagramme (nur bei ausreichender Call-Evidence)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

DiagramType = Literal["component", "dependency", "system", "sequence"]


@dataclass
class DiagramResult:
    """Ergebnis der Diagramm-Generierung."""
    
    diagram_type: DiagramType
    format: Literal["mermaid", "plantuml"]
    content: str
    node_count: int
    edge_count: int
    source_slice_hash: str
    warnings: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None
    
    def as_dict(self) -> dict[str, Any]:
        return {
            "diagram_type": self.diagram_type,
            "format": self.format,
            "content": self.content,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "source_slice_hash": self.source_slice_hash,
            "warnings": self.warnings,
            "unavailable_reason": self.unavailable_reason,
        }


class ArchitectureDiagramService:
    """Service zur deterministischen Diagramm-Erzeugung aus Architecture Slices."""
    
    ALLOWED_RELATIONSHIP_TYPES = {
        "contains": "-->",
        "uses": "..>",
        "depends_on": "..>",
        "calls": "-->>",
        "provides_context_to": "-.->",
        "implements": "..|>",
        "exposes_tool": "-->|",
        "stores": "-->>",
        "retrieves_from": "<<--",
        "governed_by": "..>:",
        "extends": "<|--",
        "composes": "--*>",
        "aggregates": "--o>",
    }
    
    def __init__(self):
        pass
    
    def generate_diagram(
        self,
        slice_data: dict[str, Any],
        diagram_type: DiagramType = "component",
        format: Literal["mermaid", "plantuml"] = "mermaid",
        max_nodes: int = 50,
        max_edges: int = 100,
    ) -> DiagramResult:
        """Generiert ein Diagramm aus einem Architecture Slice.
        
        Args:
            slice_data: Validierter Architecture Slice mit nodes und edges
            diagram_type: Typ des Diagramms (component, dependency, system, sequence)
            format: Ausgabeformat (mermaid oder plantuml)
            max_nodes: Maximale Anzahl von Nodes im Diagramm
            max_edges: Maximale Anzahl von Edges im Diagramm
            
        Returns:
            DiagramResult mit Inhalt und Metadaten
        """
        nodes = slice_data.get("nodes") or []
        edges = slice_data.get("edges") or []
        
        # Slice-Hash für Nachvollziehbarkeit
        slice_hash = self._compute_slice_hash(slice_data)
        
        # Filtere nach Diagrammtyp
        filtered_nodes, filtered_edges = self._filter_for_diagram_type(
            nodes, edges, diagram_type
        )
        
        # Budget-Checks
        warnings = []
        if len(filtered_nodes) > max_nodes:
            filtered_nodes = filtered_nodes[:max_nodes]
            node_ids = {n["id"] for n in filtered_nodes}
            filtered_edges = [e for e in filtered_edges 
                            if e.get("source") in node_ids and e.get("target") in node_ids]
            warnings.append(f"Node count truncated to {max_nodes}")
        
        if len(filtered_edges) > max_edges:
            filtered_edges = filtered_edges[:max_edges]
            warnings.append(f"Edge count truncated to {max_edges}")
        
        # Sequence-Diagramm benötigt spezielle Evidence
        if diagram_type == "sequence":
            if not self._has_sequence_evidence(filtered_edges):
                return DiagramResult(
                    diagram_type=diagram_type,
                    format=format,
                    content="",
                    node_count=0,
                    edge_count=0,
                    source_slice_hash=slice_hash,
                    warnings=warnings,
                    unavailable_reason="Insufficient call/flow evidence for sequence diagram",
                )
        
        # Generiere Diagramm
        if format == "mermaid":
            content = self._generate_mermaid(
                filtered_nodes, filtered_edges, diagram_type
            )
        else:
            content = self._generate_plantuml(
                filtered_nodes, filtered_edges, diagram_type
            )
        
        return DiagramResult(
            diagram_type=diagram_type,
            format=format,
            content=content,
            node_count=len(filtered_nodes),
            edge_count=len(filtered_edges),
            source_slice_hash=slice_hash,
            warnings=warnings,
        )
    
    def _compute_slice_hash(self, slice_data: dict[str, Any]) -> str:
        """Berechnet einen deterministischen Hash für den Slice."""
        import json
        canonical = json.dumps(
            slice_data,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    
    def _filter_for_diagram_type(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        diagram_type: DiagramType,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Filtert Nodes und Edges basierend auf dem Diagrammtyp."""
        
        if diagram_type == "system":
            # Nur System- und Subsystem-Level
            allowed_levels = {"system", "subsystem"}
            filtered_nodes = [n for n in nodes if n.get("level") in allowed_levels]
            node_ids = {n["id"] for n in filtered_nodes}
            filtered_edges = [e for e in edges 
                            if e.get("source") in node_ids and e.get("target") in node_ids]
            return filtered_nodes, filtered_edges
        
        elif diagram_type == "component":
            # System, Subsystem, Component
            allowed_levels = {"system", "subsystem", "component"}
            filtered_nodes = [n for n in nodes if n.get("level") in allowed_levels]
            node_ids = {n["id"] for n in filtered_nodes}
            filtered_edges = [e for e in edges 
                            if e.get("source") in node_ids and e.get("target") in node_ids]
            return filtered_nodes, filtered_edges
        
        elif diagram_type == "dependency":
            # Alle Nodes, aber nur depends_on/uses/calls Edges
            allowed_edge_types = {"depends_on", "uses", "calls"}
            filtered_edges = [e for e in edges 
                            if e.get("type") in allowed_edge_types]
            # Nur Nodes die an Edges beteiligt sind
            edge_node_ids = set()
            for e in filtered_edges:
                edge_node_ids.add(e.get("source"))
                edge_node_ids.add(e.get("target"))
            filtered_nodes = [n for n in nodes if n["id"] in edge_node_ids]
            return filtered_nodes, filtered_edges
        
        elif diagram_type == "sequence":
            # Nur calls-Edges für Sequenzen
            filtered_edges = [e for e in edges if e.get("type") == "calls"]
            edge_node_ids = set()
            for e in filtered_edges:
                edge_node_ids.add(e.get("source"))
                edge_node_ids.add(e.get("target"))
            filtered_nodes = [n for n in nodes if n["id"] in edge_node_ids]
            return filtered_nodes, filtered_edges
        
        return nodes, edges
    
    def _has_sequence_evidence(self, edges: list[dict[str, Any]]) -> bool:
        """Prüft ob ausreichend Evidence für ein Sequenzdiagramm vorhanden ist."""
        # Mindestens 2 call-Edges mit Evidence
        call_edges_with_evidence = [
            e for e in edges 
            if e.get("type") == "calls" and e.get("evidence")
        ]
        return len(call_edges_with_evidence) >= 2
    
    def _generate_mermaid(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        diagram_type: DiagramType,
    ) -> str:
        """Generiert Mermaid-Syntax aus Nodes und Edges."""
        lines = []
        
        # Header basierend auf Typ
        if diagram_type == "sequence":
            lines.append("sequenceDiagram")
        elif diagram_type == "system":
            lines.append("graph TB")
        else:
            lines.append("graph TB")
        
        # Styling
        lines.append("    classDef system fill:#e1f5fe,stroke:#01579b,stroke-width:2px")
        lines.append("    classDef subsystem fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px")
        lines.append("    classDef component fill:#fff3e0,stroke:#ef6c00,stroke-width:2px")
        lines.append("    classDef file fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1px")
        lines.append("    classDef symbol fill:#ffebee,stroke:#c62828,stroke-width:1px")
        lines.append("")
        
        # Nodes definieren
        for node in nodes:
            node_id = self._sanitize_mermaid_id(node["id"])
            title = self._escape_mermaid_text(node.get("title", "Unknown"))
            level = node.get("level", "component")
            
            if diagram_type == "sequence":
                # Sequenzdiagramm: Teilnehmer deklarieren
                lines.append(f"    participant {node_id} as [{title}]")
            else:
                # Flowchart: Node mit Label
                label = f"{title}"
                lines.append(f"    {node_id}[\"{label}\"]")
                lines.append(f"    class {node_id} {level}")
        
        lines.append("")
        
        # Edges definieren
        for edge in edges:
            source = self._sanitize_mermaid_id(edge.get("source", ""))
            target = self._sanitize_mermaid_id(edge.get("target", ""))
            edge_type = edge.get("type", "depends_on")
            label = edge.get("label", "")
            
            arrow = self.ALLOWED_RELATIONSHIP_TYPES.get(edge_type, "..>")
            
            if label:
                label = self._escape_mermaid_text(label)
                lines.append(f"    {source} {arrow}|{label}| {target}")
            else:
                lines.append(f"    {source} {arrow} {target}")
        
        return "\n".join(lines)
    
    def _generate_plantuml(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        diagram_type: DiagramType,
    ) -> str:
        """Generiert PlantUML-Syntax aus Nodes und Edges."""
        lines = ["@startuml"]
        
        if diagram_type == "sequence":
            lines.append("autonumber")
            lines.append("")
            # Teilnehmer
            for node in nodes:
                node_id = self._sanitize_plantuml_id(node["id"])
                title = node.get("title", "Unknown")
                lines.append(f"participant \"{title}\" as {node_id}")
        else:
            # Component/Dependency/System
            lines.append("skinparam componentStyle uml2")
            lines.append("")
            
            # Nodes nach Level gruppieren
            by_level: dict[str, list[dict[str, Any]]] = {}
            for node in nodes:
                level = node.get("level", "component")
                by_level.setdefault(level, []).append(node)
            
            for level, level_nodes in by_level.items():
                if level == "system":
                    lines.append(f"package \"System\" {{")
                elif level == "subsystem":
                    lines.append(f"package \"Subsystem\" {{")
                else:
                    lines.append(f"component \"\") {{")
                
                for node in level_nodes:
                    node_id = self._sanitize_plantuml_id(node["id"])
                    title = node.get("title", "Unknown")
                    lines.append(f"    component \"{title}\" as {node_id}")
                
                lines.append("}")
                lines.append("")
        
        # Edges
        for edge in edges:
            source = self._sanitize_plantuml_id(edge.get("source", ""))
            target = self._sanitize_plantuml_id(edge.get("target", ""))
            edge_type = edge.get("type", "depends_on")
            label = edge.get("label", "")
            
            arrow = "-->"
            if edge_type in ["uses", "depends_on"]:
                arrow = "..>"
            elif edge_type == "calls":
                arrow = "-->>"
            elif edge_type == "implements":
                arrow = "..|>"
            
            if label:
                lines.append(f"{source} {arrow}: {label};")
            else:
                lines.append(f"{source} {arrow} {target}")
        
        lines.append("@enduml")
        return "\n".join(lines)
    
    def _sanitize_mermaid_id(self, node_id: str) -> str:
        """Sanitizes eine ID für Mermaid."""
        # Ersetze Sonderzeichen
        sanitized = node_id.replace("-", "_").replace(".", "_").replace("/", "_")
        sanitized = sanitized.replace(" ", "_").replace(":", "_")
        # Entferne ungültige Zeichen
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        # Stelle sicher, dass es mit einem Buchstaben beginnt
        if sanitized and not sanitized[0].isalpha():
            sanitized = "n_" + sanitized
        return sanitized or "node_unknown"
    
    def _escape_mermaid_text(self, text: str) -> str:
        """Escapet Text für Mermaid-Labels."""
        return text.replace('"', "'").replace("\n", " ").replace("\r", "")[:100]
    
    def _sanitize_plantuml_id(self, node_id: str) -> str:
        """Sanitizes eine ID für PlantUML."""
        sanitized = node_id.replace("-", "_").replace(".", "_").replace("/", "_")
        sanitized = sanitized.replace(" ", "_").replace(":", "_")
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        if sanitized and not sanitized[0].isalpha():
            sanitized = "n_" + sanitized
        return sanitized or "node_unknown"


# Singleton-Instanz
_diagram_service: ArchitectureDiagramService | None = None


def get_architecture_diagram_service() -> ArchitectureDiagramService:
    """Returns die singleton ArchitectureDiagramService Instanz."""
    global _diagram_service
    if _diagram_service is None:
        _diagram_service = ArchitectureDiagramService()
    return _diagram_service
