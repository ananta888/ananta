from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from agent.services.codecompass_architecture_budget import resolve_architecture_budget
from agent.services.codecompass_architecture_diagram_service import (
    CodeCompassArchitectureDiagramService,
)
from agent.services.codecompass_architecture_slice_service import (
    CodeCompassArchitectureSliceService,
    decode_handle,
    encode_handle,
)
from agent.services.codecompass_architecture_summary_service import (
    CodeCompassArchitectureSummaryService,
)
from agent.services.codecompass_context_planner_service import CodeCompassContextPlanner
from agent.services.tools import execute_ananta_tool
from worker.retrieval.codecompass_hierarchical_architecture import project_hierarchy

SCHEMA = json.loads(Path("schemas/codecompass.hierarchical-architecture-context.v1.json").read_text())


def _records():
    return [
        {"id": "ananta", "kind": "system", "name": "Ananta", "path": "README.md", "content": "hub-worker platform"},
        {"id": "cc", "kind": "subsystem", "name": "CodeCompass", "path": "docs/codecompass.md", "parent_id": "ananta"},
        {"id": "planner", "kind": "component_service", "name": "Context Planner", "path": "agent/services/codecompass_context_planner_service.py", "parent_id": "cc"},
        {"id": "planner-file", "kind": "file", "name": "codecompass_context_planner_service.py", "path": "agent/services/codecompass_context_planner_service.py", "parent_id": "planner"},
        {"id": "plan-fn", "kind": "python_function", "name": "plan_architecture_prefill", "path": "agent/services/codecompass_context_planner_service.py", "parent_id": "planner-file"},
        {"id": "mystery", "kind": "weird_blob", "name": "???", "path": ""},
    ]


def _edges():
    return [
        {"source": "ananta", "target": "cc", "type": "contains"},
        {"source": "cc", "target": "planner", "type": "contains"},
        {"source": "planner", "target": "planner-file", "type": "contains"},
        {"source": "planner-file", "target": "plan-fn", "type": "contains"},
        {"source": "planner", "target": "cc", "type": "calls_probable_target"},
    ]


def _capability():
    return {"workspace_id": "ws-1", "revision": "rev-1", "tenant_id": "t1", "allowed_paths": ["agent", "docs", "README.md"]}


def test_schema_accepts_five_level_slice() -> None:
    slice_payload = CodeCompassArchitectureSliceService().build_slice(
        query="Was ist CodeCompass im Ananta-System?",
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    jsonschema.validate(slice_payload, SCHEMA)
    levels = {node["level"] for node in slice_payload["nodes"]}
    assert {"system", "subsystem", "component"}.issubset(levels)
    assert slice_payload["nodes"]
    assert all(node.get("source_refs") for node in slice_payload["nodes"])


def test_schema_rejects_node_without_level() -> None:
    payload = CodeCompassArchitectureSliceService().build_slice(
        query="q",
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    del payload["nodes"][0]["level"]
    try:
        jsonschema.validate(payload, SCHEMA)
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected schema failure")


def test_projection_is_deterministic_and_keeps_unknown() -> None:
    first = project_hierarchy(records=_records(), edges=_edges(), revision="rev-1")
    second = project_hierarchy(records=list(reversed(_records())), edges=list(reversed(_edges())), revision="rev-1")
    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    assert first["unknown_count"] >= 1


def test_projection_adapts_persisted_graph_vocabulary() -> None:
    projected = project_hierarchy(
        records=[
            {"id": "repo", "kind": "repository", "name": "Ananta"},
            {"id": "agent", "kind": "directory", "name": "agent", "file": "agent"},
            {"id": "service", "kind": "directory", "name": "services", "file": "agent/services"},
            {"id": "file", "kind": "source_file", "name": "codecompass.py", "file": "agent/services/codecompass.py"},
        ],
        edges=[
            {"source_id": "repo", "target_id": "agent", "edge_type": "contains_directory", "edge_id": "e1"},
            {"source_id": "agent", "target_id": "service", "edge_type": "contains_directory", "edge_id": "e2"},
            {"source_id": "service", "target_id": "file", "edge_type": "contains_file", "edge_id": "e3"},
        ],
        revision="rev",
    )

    assert [node["level"] for node in projected["nodes"]] == [
        "system", "subsystem", "component", "file"
    ]
    assert len(projected["edges"]) == 3
    assert {edge["relation"] for edge in projected["edges"]} == {"contains"}


def test_budget_never_exceeds_caps() -> None:
    huge = _records() * 8
    slice_payload = CodeCompassArchitectureSliceService().build_slice(
        query="CodeCompass",
        records=huge,
        edges=_edges(),
        capability=_capability(),
        profile="overview",
        parent_max_tokens=400,
    )
    budget = slice_payload["budgets"]
    assert len(slice_payload["nodes"]) <= budget["max_nodes"]
    assert len(slice_payload["edges"]) <= budget["max_edges"]
    assert budget["used_tokens"] <= budget["max_tokens"]
    assert budget["max_tokens"] <= 400


def test_capability_fail_closed_and_redaction() -> None:
    service = CodeCompassArchitectureSliceService()
    try:
        service.build_slice(query="x", records=_records(), capability={"workspace_id": "", "revision": ""})
        raise AssertionError("empty scope must fail")
    except ValueError as exc:
        assert str(exc) == "empty_scope"
    leaked = service.build_slice(
        query="secret",
        records=_records() + [{"id": "sec", "kind": "file", "name": "secret", "path": "secret/key.py", "content": "password=hunter2"}],
        capability=_capability(),
    )
    assert all("secret/key.py" not in str(node.get("path") or "") for node in leaked["nodes"])


def test_system_question_prefers_system_before_files() -> None:
    slice_payload = CodeCompassArchitectureSliceService().build_slice(
        query="Was ist CodeCompass im Ananta-System?",
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    first_levels = [node["level"] for node in slice_payload["nodes"][:3]]
    assert "system" in first_levels or "subsystem" in first_levels
    assert slice_payload["nodes"][0]["level"] in {"system", "subsystem", "component"}


def test_broad_codecompass_query_prefers_explanatory_entrypoints() -> None:
    records = [
        {"id": "migration", "kind": "source_file", "name": "codecompass_hardening.py", "file": "agent/services/codecompass_hardening.py"},
        {"id": "architecture", "kind": "source_file", "name": "codecompass_architecture_slice_service.py", "file": "agent/services/codecompass_architecture_slice_service.py"},
    ]

    payload = CodeCompassArchitectureSliceService().build_slice(
        query="Erkläre CodeCompass",
        records=records,
        capability={"workspace_id": "ws-1", "revision": "rev-1"},
    )

    assert payload["nodes"][0]["id"] == "architecture"


def test_summary_cache_invalidates_on_revision_change() -> None:
    service = CodeCompassArchitectureSummaryService()
    node = {"id": "cc", "title": "CodeCompass", "level": "subsystem", "source_refs": ["docs/codecompass.md"], "short_summary": "context layer"}
    first = service.summarize(node, revision="rev-1")
    second = service.summarize(node, revision="rev-2")
    assert first["cache_key"] != second["cache_key"]
    empty = service.summarize({"id": "x", "title": "x", "level": "unknown"}, revision="rev-1")
    assert empty["status"] == "summary_unavailable"


def test_stale_handle_is_rejected() -> None:
    handle = encode_handle(revision="rev-1", node_id="cc")
    try:
        decode_handle(handle, revision="rev-2")
        raise AssertionError("stale handle must fail")
    except ValueError as exc:
        assert str(exc) == "stale_architecture_handle"


def test_zoom_roundtrip() -> None:
    service = CodeCompassArchitectureSliceService()
    overview = service.build_slice(query="CodeCompass", records=_records(), edges=_edges(), capability=_capability())
    handle = next(node["handle"] for node in overview["nodes"] if node["id"] == "cc")
    expanded = service.navigate(
        overview,
        action="expand",
        handle=handle,
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    back = service.navigate(
        expanded,
        action="overview",
        handle=handle,
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    assert any(node["id"] == "planner" for node in expanded["nodes"])
    assert any(node["level"] == "system" for node in back["nodes"])


def test_diagram_stays_on_slice_and_sequence_needs_calls() -> None:
    slice_payload = CodeCompassArchitectureSliceService().build_slice(
        query="planner",
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    diagram = CodeCompassArchitectureDiagramService().render(slice_payload, kind="component")
    assert diagram["status"] == "ok"
    assert "flowchart LR" in diagram["diagram"]
    for node in slice_payload["nodes"]:
        assert node["id"] in diagram["diagram"]
    missing = CodeCompassArchitectureDiagramService().render({"nodes": [], "edges": []}, kind="sequence")
    assert missing["reason"] == "sequence_evidence_missing"


def test_planner_can_disable_prefill() -> None:
    planner = CodeCompassContextPlanner()
    disabled = planner.plan_architecture_prefill(query="x", enabled=False)
    assert disabled["disabled"] is True
    enabled = planner.plan_architecture_prefill(
        query="CodeCompass",
        records=_records(),
        edges=_edges(),
        capability=_capability(),
    )
    assert enabled["nodes"]


def test_unknown_handle_tool_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.services.tools.codecompass_architecture_tools._load_architecture_graph",
        lambda arguments=None: (_records(), _edges()),
    )
    result = execute_ananta_tool(
        tool_name="codecompass.architecture_expand",
        arguments={"handle": "hac:deadbeef:nope", "revision": "rev-1"},
        workspace_dir=".",
        tool_call_id="t1",
    )
    assert result["status"] == "error"


def test_architecture_tools_overview_and_expand(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.services.tools.codecompass_architecture_tools._load_architecture_graph",
        lambda arguments=None: (_records(), _edges()),
    )
    overview = execute_ananta_tool(
        tool_name="codecompass.architecture_overview",
        arguments={"query": "Was ist CodeCompass im Ananta-System?", "revision": "rev-1"},
        workspace_dir=".",
        tool_call_id="t2",
    )
    assert overview["status"] == "ok"
    handle = overview["data"]["architecture"]["expansion_handles"][0]
    expanded = execute_ananta_tool(
        tool_name="codecompass.architecture_expand",
        arguments={"handle": handle, "revision": "rev-1"},
        workspace_dir=".",
        tool_call_id="t3",
    )
    assert expanded["status"] == "ok"
    assert expanded["data"]["architecture"]["truncated"] in {True, False}
