"""Tests for the notation-pattern gates (NOT-002).

Covers all 8 notation gates added to PatternGateService, including:

* pass-case for each notation pattern (rendered output via NotationRenderer
  must satisfy the gate)
* fail-case detection (malformed / missing elements, broken BPMN XML)
* gate service dispatcher routes notation pattern_ids correctly
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.services.notation_renderer import get_notation_renderer
from agent.services.pattern_gate_service import get_pattern_gate_service


# ---------------------------------------------------------------------------
# Reuse the same canonical payloads as the renderer tests
# ---------------------------------------------------------------------------


MERMAID_CLASS_PARAMS = {
    "diagram_title": "Order Strategy",
    "direction": "LR",
    "classes": [
        {"name": "OrderStrategy", "stereotype": "interface",
         "methods": ["calculate(order: Order): Money"]},
        {"name": "StandardPricing", "methods": ["calculate(order: Order): Money"]},
        {"name": "Order", "fields": ["total: Money"]},
    ],
    "relationships": [
        {"type": "realization", "from": "StandardPricing", "to": "OrderStrategy"},
        {"type": "association", "from": "Order", "to": "OrderStrategy",
         "to_label": "1"},
    ],
}

MERMAID_SEQUENCE_PARAMS = {
    "participants": [
        {"id": "User", "kind": "actor", "label": "Customer"},
        {"id": "Service", "kind": "control", "label": "Order Service"},
    ],
    "messages": [
        {"from": "User", "to": "Service", "text": "place order", "type": "sync",
         "activate": True},
        {"from": "Service", "to": "User", "text": "receipt", "type": "return"},
    ],
}

MERMAID_STATE_PARAMS = {
    "states": [{"id": "A"}, {"id": "B"}],
    "transitions": [
        {"from": "[*]", "to": "A"},
        {"from": "A", "to": "B"},
        {"from": "B", "to": "[*]"},
    ],
}

MERMAID_USECASE_PARAMS = {
    "system_name": "Library",
    "actors": [{"id": "Member", "label": "Member"}],
    "use_cases": [{"id": "Borrow", "label": "Borrow"}],
    "associations": [{"actor": "Member", "use_case": "Borrow"}],
}

MERMAID_ACTIVITY_PARAMS = {
    "nodes": [
        {"id": "Start", "shape": "initial"},
        {"id": "Do", "shape": "action", "label": "Do"},
        {"id": "Done", "shape": "final"},
    ],
    "edges": [
        {"from": "Start", "to": "Do"},
        {"from": "Do", "to": "Done"},
    ],
}

BPMN_PROCESS_PARAMS = {
    "definitions_id": "Definitions_1",
    "process_id": "Process_1",
    "elements": [
        {"type": "startEvent", "id": "S"},
        {"type": "userTask", "id": "T"},
        {"type": "endEvent", "id": "E"},
    ],
    "flows": [
        {"id": "F1", "sourceRef": "S", "targetRef": "T"},
        {"id": "F2", "sourceRef": "T", "targetRef": "E"},
    ],
}

BPMN_POOL_LANE_PARAMS = {
    "definitions_id": "Definitions_1",
    "process_id": "Process_1",
    "elements": [
        {"type": "startEvent", "id": "S"},
        {"type": "endEvent", "id": "E"},
    ],
    "flows": [{"id": "F1", "sourceRef": "S", "targetRef": "E"}],
    "lanes": [{"id": "L", "name": "Lane", "flow_node_refs": ["S", "E"]}],
}

BPMN_COLLABORATION_PARAMS = {
    "definitions_id": "Definitions_1",
    "participants": [
        {"id": "PA", "name": "A", "process_id": "PA",
         "elements": [
             {"type": "startEvent", "id": "SA"},
             {"type": "endEvent", "id": "EA"},
         ],
         "flows": [{"id": "FA", "sourceRef": "SA", "targetRef": "EA"}]},
        {"id": "PB", "name": "B", "process_id": "PB",
         "elements": [
             {"type": "startEvent", "id": "SB"},
             {"type": "endEvent", "id": "EB"},
         ],
         "flows": [{"id": "FB", "sourceRef": "SB", "targetRef": "EB"}]},
    ],
    "message_flows": [
        {"id": "MF", "source_ref": "PA", "target_ref": "PB"},
    ],
}


def _render_and_gate(pid: str, lang: str, params: dict, tmp_path: Path):
    r = get_notation_renderer()
    g = get_pattern_gate_service()
    artifact = r.render(
        pattern_plan={"pattern_id": pid, "language": lang, "parameters": params},
        target_root=str(tmp_path),
    )
    return g.check(
        pattern_id=pid,
        language=lang,
        output_files=[artifact.output_filename],
        workspace_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pid,lang,params", [
    ("mermaid.class", "mermaid", MERMAID_CLASS_PARAMS),
    ("mermaid.sequence", "mermaid", MERMAID_SEQUENCE_PARAMS),
    ("mermaid.state", "mermaid", MERMAID_STATE_PARAMS),
    ("mermaid.usecase", "mermaid", MERMAID_USECASE_PARAMS),
    ("mermaid.activity", "mermaid", MERMAID_ACTIVITY_PARAMS),
    ("bpmn.process", "bpmn", BPMN_PROCESS_PARAMS),
    ("bpmn.pool_lane", "bpmn", BPMN_POOL_LANE_PARAMS),
    ("bpmn.collaboration", "bpmn", BPMN_COLLABORATION_PARAMS),
])
def test_gate_passes_for_valid_rendered_output(pid, lang, params, tmp_path):
    result = _render_and_gate(pid, lang, params, tmp_path)
    assert result.passed, (
        f"gate failed for {pid}: "
        f"failed_checks={result.failed_checks} "
        f"details={[(d.name, d.message) for d in result.details if not d.passed]}"
    )


# ---------------------------------------------------------------------------
# Fail paths
# ---------------------------------------------------------------------------


def test_mermaid_class_gate_fails_for_missing_class_blocks(tmp_path):
    # Write a syntactically-broken mermaid file by hand
    (tmp_path / "diagram.mmd").write_text(
        "flowchart LR\n  A --> B\n", encoding="utf-8"
    )
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="mermaid.class",
        language="mermaid",
        output_files=["diagram.mmd"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "starts_with_classdiagram" in result.failed_checks


def test_mermaid_sequence_gate_fails_when_too_few_participants(tmp_path):
    (tmp_path / "diagram.mmd").write_text(
        "sequenceDiagram\n  A->>A: self\n", encoding="utf-8"
    )
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="mermaid.sequence",
        language="mermaid",
        output_files=["diagram.mmd"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "has_participants" in result.failed_checks


def test_mermaid_state_gate_fails_without_initial_pseudostate(tmp_path):
    (tmp_path / "diagram.mmd").write_text(
        "stateDiagram-v2\n  A --> B\n", encoding="utf-8"
    )
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="mermaid.state",
        language="mermaid",
        output_files=["diagram.mmd"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "has_initial_pseudostate" in result.failed_checks


def test_mermaid_usecase_gate_fails_without_subgraph(tmp_path):
    (tmp_path / "diagram.mmd").write_text(
        "flowchart LR\n  Member([\"Member\"])\n", encoding="utf-8"
    )
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="mermaid.usecase",
        language="mermaid",
        output_files=["diagram.mmd"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "has_system_boundary" in result.failed_checks


def test_mermaid_activity_gate_fails_without_final_node(tmp_path):
    (tmp_path / "diagram.mmd").write_text(
        "flowchart TB\n  Start(((\"Start\")))\n  Start --> Start\n", encoding="utf-8"
    )
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="mermaid.activity",
        language="mermaid",
        output_files=["diagram.mmd"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "has_final_node" in result.failed_checks


def test_bpmn_gate_fails_on_malformed_xml(tmp_path):
    (tmp_path / "process.bpmn").write_text(
        "<?xml version=\"1.0\"?>\n<not-closed>", encoding="utf-8"
    )
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="bpmn.process",
        language="bpmn",
        output_files=["process.bpmn"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "xml_parses" in result.failed_checks


def test_bpmn_gate_fails_when_namespace_wrong(tmp_path):
    bad = (
        '<?xml version="1.0"?>\n'
        '<definitions xmlns="http://wrong-namespace">'
        '<process id="P"/></definitions>'
    )
    (tmp_path / "process.bpmn").write_text(bad, encoding="utf-8")
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="bpmn.process",
        language="bpmn",
        output_files=["process.bpmn"],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "bpmn_namespace" in result.failed_checks


def test_bpmn_pool_lane_gate_fails_without_lane_set(tmp_path):
    # Valid BPMN but no laneSet -> lane checks fail
    r = get_notation_renderer()
    art = r.render(
        pattern_plan={
            "pattern_id": "bpmn.process",
            "language": "bpmn",
            "parameters": BPMN_PROCESS_PARAMS,
        },
        target_root=str(tmp_path),
    )
    # Re-label it as pool_lane to drive the wrong gate
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="bpmn.pool_lane",
        language="bpmn",
        output_files=[art.output_filename],
        workspace_root=tmp_path,
    )
    assert not result.passed
    assert "has_lane_set" in result.failed_checks


def test_bpmn_collaboration_gate_fails_with_only_one_participant(tmp_path):
    params = {
        "definitions_id": "D",
        "participants": [
            {"id": "PA", "name": "A", "process_id": "PA",
             "elements": [
                 {"type": "startEvent", "id": "SA"},
                 {"type": "endEvent", "id": "EA"},
             ],
             "flows": [{"id": "FA", "sourceRef": "SA", "targetRef": "EA"}]},
        ],
        "message_flows": [],
    }
    result = _render_and_gate("bpmn.collaboration", "bpmn", params, tmp_path)
    assert not result.passed
    assert "has_participants" in result.failed_checks


# ---------------------------------------------------------------------------
# Dispatcher coverage
# ---------------------------------------------------------------------------


def test_gate_service_dispatches_notation_pattern_ids():
    g = get_pattern_gate_service()
    for pid in [
        "mermaid.class", "mermaid.sequence", "mermaid.state",
        "mermaid.usecase", "mermaid.activity",
        "bpmn.process", "bpmn.pool_lane", "bpmn.collaboration",
    ]:
        result = g.check(
            pattern_id=pid,
            language="mermaid" if pid.startswith("mermaid.") else "bpmn",
            output_files=[],
            workspace_root=Path(tempfile.gettempdir()),
        )
        # Empty output -> file_present must fail (so passed=False overall)
        assert not result.passed, f"{pid}: gate should fail on empty output"
        assert "file_present" in result.failed_checks


def test_gate_result_to_dict_is_serialisable():
    g = get_pattern_gate_service()
    result = g.check(
        pattern_id="mermaid.class",
        language="mermaid",
        output_files=[],
        workspace_root=Path(tempfile.gettempdir()),
    )
    d = result.to_dict()
    assert d["pattern_id"] == "mermaid.class"
    assert d["language"] == "mermaid"
    assert isinstance(d["checked_files"], list)
    assert isinstance(d["passed_checks"], list)
    assert isinstance(d["failed_checks"], list)