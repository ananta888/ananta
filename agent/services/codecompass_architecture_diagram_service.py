"""Deterministic diagrams from an already authorized architecture slice."""

from __future__ import annotations

from typing import Any, Mapping

_SAFE = str.maketrans({'"': "'", "[": "(", "]": ")", "{": "(", "}": ")"})


def _label(node: Mapping[str, Any]) -> str:
    title = str(node.get("title") or node.get("id") or "node").translate(_SAFE)
    return title[:48]


class CodeCompassArchitectureDiagramService:
    def render(
        self,
        slice_payload: Mapping[str, Any],
        *,
        kind: str = "component",
    ) -> dict[str, Any]:
        nodes = [item for item in list(slice_payload.get("nodes") or []) if isinstance(item, dict)]
        edges = [item for item in list(slice_payload.get("edges") or []) if isinstance(item, dict)]
        node_ids = {str(item.get("id") or "") for item in nodes}
        kind_token = str(kind or "component").strip().lower()
        if kind_token == "sequence":
            call_edges = [edge for edge in edges if edge.get("relation") == "calls"]
            if not call_edges:
                return {
                    "status": "unavailable",
                    "reason": "sequence_evidence_missing",
                    "format": "mermaid",
                    "diagram": "",
                }
            lines = ["sequenceDiagram"]
            for edge in call_edges:
                lines.append(f"    {_label(_node(nodes, edge['source']))}->>{_label(_node(nodes, edge['target']))}: calls")
            return {"status": "ok", "reason": "", "format": "mermaid", "diagram": "\n".join(lines)}

        lines = ["flowchart LR"]
        for node in nodes:
            lines.append(f'    {node["id"]}["{_label(node)}"]')
        for edge in edges:
            if edge.get("source") in node_ids and edge.get("target") in node_ids:
                lines.append(f'    {edge["source"]} -->|{edge["relation"]}| {edge["target"]}')
        return {"status": "ok", "reason": "", "format": "mermaid", "diagram": "\n".join(lines)}


def _node(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any]:
    for item in nodes:
        if item.get("id") == node_id:
            return item
    return {"id": node_id, "title": node_id}


_diagram_service = CodeCompassArchitectureDiagramService()


def get_codecompass_architecture_diagram_service() -> CodeCompassArchitectureDiagramService:
    return _diagram_service
