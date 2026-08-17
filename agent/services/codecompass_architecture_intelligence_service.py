"""Hub facade for architecture intelligence. Analysis is a projection, not SoT."""

from __future__ import annotations

from typing import Any, Mapping

from ananta_codecompass.architecture_intelligence.analyze import analyze_architecture
from ananta_codecompass.architecture_intelligence.diff import diff_graphs
from ananta_codecompass.architecture_intelligence.exporters import (
    export_cypher,
    export_graphml,
    export_html,
    export_json,
    export_markdown,
    export_obsidian,
)
from ananta_codecompass.architecture_intelligence.graph_projection import project_graph


class CodeCompassArchitectureIntelligenceService:
    def analyze(self, records: Mapping[str, Any], *, snapshot_ref: str = "", revision: str = "") -> dict[str, Any]:
        return analyze_architecture(records, snapshot_ref=snapshot_ref, revision=revision)

    def diff(self, old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
        return diff_graphs(old, new)

    def export(self, result: Mapping[str, Any], *, fmt: str, records: Mapping[str, Any] | None = None) -> str:
        if fmt == "json":
            return export_json(result)
        if fmt == "md":
            return export_markdown(result)
        if fmt == "html":
            return export_html(result)
        if fmt == "obsidian":
            return export_obsidian(result)
        projection = project_graph(records or {"nodes": [], "edges": []})
        if fmt == "graphml":
            return export_graphml(projection)
        if fmt == "cypher":
            return export_cypher(projection)
        raise ValueError("unknown_export_format")


_service = CodeCompassArchitectureIntelligenceService()


def get_codecompass_architecture_intelligence_service() -> CodeCompassArchitectureIntelligenceService:
    return _service
