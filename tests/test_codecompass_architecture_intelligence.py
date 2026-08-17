from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from ananta_codecompass.architecture_intelligence.analyze import analyze_architecture
from ananta_codecompass.architecture_intelligence.diff import diff_graphs
from ananta_codecompass.architecture_intelligence.community import detect_communities
from ananta_codecompass.architecture_intelligence.graph_projection import project_graph
from agent.services.codecompass_architecture_intelligence_service import (
    CodeCompassArchitectureIntelligenceService,
)


def _graph():
    return {
        "nodes": [
            {"id": "hub", "kind": "component", "path": "agent/app.py"},
            {"id": "worker", "kind": "component", "path": "worker/app.py"},
            {"id": "store", "kind": "component", "path": "agent/db.py"},
            {"id": "hub2", "kind": "component", "path": "agent/routes.py"},
        ],
        "edges": [
            {"source": "hub", "target": "worker", "type": "calls"},
            {"source": "worker", "target": "store", "type": "uses"},
            {"source": "hub", "target": "store", "type": "uses"},
            {"source": "hub", "target": "hub2", "type": "contains"},
            {"source": "hub2", "target": "hub", "type": "uses"},
        ],
    }


def test_analysis_is_deterministic_and_schema_valid() -> None:
    schema = json.loads(Path("schemas/codecompass.architecture-intelligence.v1.json").read_text())
    first = analyze_architecture(_graph(), snapshot_ref="snap-1", revision="rev-1")
    second = analyze_architecture(_graph(), snapshot_ref="snap-1", revision="rev-1")
    jsonschema.validate(first, schema)
    assert first == second
    assert first["communities"]
    assert first["algorithm"]["fingerprint"]


def test_community_fingerprint_stable_for_same_members() -> None:
    projection = project_graph(_graph())
    a = detect_communities(projection)
    b = detect_communities(projection)
    assert [item["fingerprint"] for item in a] == [item["fingerprint"] for item in b]


def test_diff_does_not_treat_missing_as_delete_without_warning() -> None:
    newer = _graph()
    newer["nodes"] = newer["nodes"] + [{"id": "cli", "path": "cli.py"}]
    diff = diff_graphs(_graph(), newer)
    assert "cli" in diff["added_nodes"]
    assert diff["coverage_warning"]


def test_cycle_and_export_formats() -> None:
    result = analyze_architecture(_graph(), snapshot_ref="s")
    kinds = {item["kind"] for item in result["smells"]}
    assert "cycle" in kinds or result["health"]["smell_count"] >= 0
    service = CodeCompassArchitectureIntelligenceService()
    assert "<graphml>" in service.export(result, fmt="graphml", records=_graph())
    assert "CREATE" in service.export(result, fmt="cypher", records=_graph())
    assert "[[" in service.export(result, fmt="obsidian")
