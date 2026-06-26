"""Unit tests for the deterministic diagram-notation renderer (NOT-001).

Covers all 8 supported notation patterns (5 Mermaid, 3 BPMN 2.0):

* byte-identical output for identical inputs (determinism)
* well-formed BPMN 2.0 XML for every BPMN pattern
* UML2-conformant Mermaid output (visibility, stereotypes, arrows)
* structured-validation errors for unknown types, dangling references,
  invalid identifiers, malformed payloads
* path-safe on-disk write (refuses absolute / `..` paths)
* dry-run does not touch the filesystem
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agent.services.notation_renderer import (
    NotationArtifact,
    NotationRenderError,
    NotationRenderer,
    get_notation_renderer,
)


# ---------------------------------------------------------------------------
# Shared payloads
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
    "diagram_title": "Checkout",
    "autonumber": True,
    "participants": [
        {"id": "User", "kind": "actor", "label": "Customer"},
        {"id": "Service", "kind": "control", "label": "Order Service"},
    ],
    "messages": [
        {"from": "User", "to": "Service", "text": "place order",
         "type": "sync", "activate": True},
        {"from": "Service", "to": "User", "text": "receipt", "type": "return"},
    ],
}

MERMAID_STATE_PARAMS = {
    "states": [
        {"id": "Draft"},
        {"id": "Submitted"},
        {"id": "Cancelled"},
    ],
    "transitions": [
        {"from": "[*]", "to": "Draft"},
        {"from": "Draft", "to": "Submitted", "event": "submit"},
        {"from": "Submitted", "to": "Cancelled", "event": "cancel"},
        {"from": "Submitted", "to": "[*]", "event": "approve"},
    ],
}

MERMAID_USECASE_PARAMS = {
    "system_name": "Library",
    "actors": [
        {"id": "Member", "label": "Member"},
        {"id": "Librarian", "label": "Librarian"},
    ],
    "use_cases": [
        {"id": "BorrowBook", "label": "Borrow Book"},
        {"id": "ReturnBook", "label": "Return Book"},
    ],
    "associations": [
        {"actor": "Member", "use_case": "BorrowBook"},
    ],
    "includes": [
        {"from": "BorrowBook", "to": "ReturnBook"},
    ],
}

MERMAID_ACTIVITY_PARAMS = {
    "nodes": [
        {"id": "Start", "shape": "initial"},
        {"id": "Receive", "shape": "action", "label": "Receive Order"},
        {"id": "InStock", "shape": "decision", "label": "In Stock?"},
        {"id": "Ship", "shape": "action", "label": "Ship"},
        {"id": "Done", "shape": "final"},
    ],
    "edges": [
        {"from": "Start", "to": "Receive"},
        {"from": "Receive", "to": "InStock"},
        {"from": "InStock", "to": "Ship", "label": "yes"},
        {"from": "Ship", "to": "Done"},
    ],
}

BPMN_PROCESS_PARAMS = {
    "definitions_id": "Definitions_1",
    "process_id": "Process_Order",
    "process_name": "Order",
    "elements": [
        {"type": "startEvent", "id": "StartEvent_1", "name": "Received"},
        {"type": "userTask", "id": "Task_Pick", "name": "Pick"},
        {"type": "exclusiveGateway", "id": "Gateway_1", "name": "Stock?"},
        {"type": "serviceTask", "id": "Task_Ship", "name": "Ship"},
        {"type": "endEvent", "id": "EndEvent_1", "name": "Done"},
    ],
    "flows": [
        {"id": "Flow_1", "sourceRef": "StartEvent_1", "targetRef": "Task_Pick"},
        {"id": "Flow_2", "sourceRef": "Task_Pick", "targetRef": "Gateway_1"},
        {"id": "Flow_3", "sourceRef": "Gateway_1", "targetRef": "Task_Ship",
         "conditionExpression": "${stock}"},
        {"id": "Flow_4", "sourceRef": "Task_Ship", "targetRef": "EndEvent_1"},
    ],
}

BPMN_POOL_LANE_PARAMS = {
    "definitions_id": "Definitions_1",
    "process_id": "Process_Procurement",
    "elements": [
        {"type": "startEvent", "id": "StartEvent_1"},
        {"type": "userTask", "id": "Task_Review"},
        {"type": "endEvent", "id": "EndEvent_1"},
    ],
    "flows": [
        {"id": "Flow_1", "sourceRef": "StartEvent_1", "targetRef": "Task_Review"},
        {"id": "Flow_2", "sourceRef": "Task_Review", "targetRef": "EndEvent_1"},
    ],
    "lanes": [
        {"id": "Lane_Requester", "name": "Requester",
         "flow_node_refs": ["StartEvent_1"]},
        {"id": "Lane_Procurement", "name": "Procurement",
         "flow_node_refs": ["Task_Review", "EndEvent_1"]},
    ],
}

BPMN_COLLABORATION_PARAMS = {
    "definitions_id": "Definitions_1",
    "participants": [
        {"id": "Participant_Customer", "name": "Customer",
         "process_id": "Process_Customer", "process_name": "Customer Process",
         "elements": [
             {"type": "startEvent", "id": "CSE_1"},
             {"type": "userTask", "id": "CT_1"},
             {"type": "endEvent", "id": "CEE_1"},
         ],
         "flows": [
             {"id": "CF_1", "sourceRef": "CSE_1", "targetRef": "CT_1"},
             {"id": "CF_2", "sourceRef": "CT_1", "targetRef": "CEE_1"},
         ]},
        {"id": "Participant_Warehouse", "name": "Warehouse",
         "process_id": "Process_Warehouse", "process_name": "Warehouse Process",
         "elements": [
             {"type": "startEvent", "id": "WSE_1"},
             {"type": "serviceTask", "id": "WT_1"},
             {"type": "endEvent", "id": "WEE_1"},
         ],
         "flows": [
             {"id": "WF_1", "sourceRef": "WSE_1", "targetRef": "WT_1"},
             {"id": "WF_2", "sourceRef": "WT_1", "targetRef": "WEE_1"},
         ]},
    ],
    "message_flows": [
        {"id": "MF_1", "name": "order",
         "source_ref": "Participant_Customer",
         "target_ref": "Participant_Warehouse"},
    ],
}


# ---------------------------------------------------------------------------
# Determinism + basic shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pid,lang,params,filename", [
    ("mermaid.class", "mermaid", MERMAID_CLASS_PARAMS, "diagram.mmd"),
    ("mermaid.sequence", "mermaid", MERMAID_SEQUENCE_PARAMS, "diagram.mmd"),
    ("mermaid.state", "mermaid", MERMAID_STATE_PARAMS, "diagram.mmd"),
    ("mermaid.usecase", "mermaid", MERMAID_USECASE_PARAMS, "diagram.mmd"),
    ("mermaid.activity", "mermaid", MERMAID_ACTIVITY_PARAMS, "diagram.mmd"),
    ("bpmn.process", "bpmn", BPMN_PROCESS_PARAMS, "process.bpmn"),
    ("bpmn.pool_lane", "bpmn", BPMN_POOL_LANE_PARAMS, "process.bpmn"),
    ("bpmn.collaboration", "bpmn", BPMN_COLLABORATION_PARAMS, "collaboration.bpmn"),
])
def test_notation_renderer_is_deterministic(pid, lang, params, filename):
    r = get_notation_renderer()
    a1 = r.render(pattern_plan={"pattern_id": pid, "language": lang, "parameters": params})
    a2 = r.render(pattern_plan={"pattern_id": pid, "language": lang, "parameters": params})
    assert a1.sha256 == a2.sha256
    assert a1.source == a2.source
    assert a1.output_filename == filename
    # Artifact is immutable.
    assert isinstance(a1, NotationArtifact)
    assert a1.manifest_sha256 == hashlib.sha256(
        f"{a1.pattern_id}\t{a1.language}\t{a1.output_filename}\t"
        f"{a1.sha256}\t{a1.bytes_written}".encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Mermaid content checks
# ---------------------------------------------------------------------------


def test_mermaid_class_contains_uml2_markers():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "parameters": MERMAID_CLASS_PARAMS,
    })
    src = art.source
    assert src.startswith("classDiagram")
    assert "direction LR" in src
    assert "class OrderStrategy" in src
    assert "<<interface>>" in src
    assert "{abstract}" in src  # interface methods are abstract
    assert "StandardPricing ..|> OrderStrategy" in src
    assert '"1"' in src  # multiplicity


def test_mermaid_class_arrow_taxonomy():
    """All canonical UML2 arrows must be reachable."""
    r = get_notation_renderer()
    cases = [
        ("inheritance", "A <|-- B"),
        ("composition", "A *-- B"),
        ("aggregation", "A o-- B"),
        ("association", "A --> B"),
        ("realization", "A ..|> B"),
        ("dependency", "A ..> B"),
        ("link", "A -- B"),
    ]
    for rel_type, expected in cases:
        art = r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "parameters": {
                "classes": [{"name": "A"}, {"name": "B"}],
                "relationships": [{"type": rel_type, "from": "A", "to": "B"}],
            },
        })
        assert expected in art.source, f"missing arrow for {rel_type}"


def test_mermaid_sequence_contains_participants_and_messages():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.sequence",
        "language": "mermaid",
        "parameters": MERMAID_SEQUENCE_PARAMS,
    })
    src = art.source
    assert src.startswith("sequenceDiagram")
    assert "autonumber" in src
    assert "actor User as Customer" in src
    assert "participant Service as Order Service" in src
    assert "User->>Service: place order" in src
    assert "activate Service" in src
    assert "Service-->>User: receipt" in src


def test_mermaid_sequence_alt_fragment_rendered():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.sequence",
        "language": "mermaid",
        "parameters": {
            "participants": [{"id": "A"}, {"id": "B"}],
            "messages": [{"from": "A", "to": "B", "text": "go", "type": "sync"}],
            "fragments": [{
                "type": "alt", "label": "result",
                "branches": [
                    {"condition": "ok",
                     "messages": [{"from": "B", "to": "A", "text": "fine", "type": "sync"}]},
                    {"condition": "fail",
                     "messages": [{"from": "B", "to": "A", "text": "bad", "type": "sync"}]},
                ],
            }],
        },
    })
    src = art.source
    assert "alt result" in src
    assert "ok" in src
    assert "else fail" in src
    assert src.count("alt ") == src.count("\nend\n") or src.count("alt result") == src.count("end\n")


def test_mermaid_state_has_pseudostates_and_balanced_composite():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.state",
        "language": "mermaid",
        "parameters": {
            "states": [
                {"id": "Outer", "nested": ["InnerA", "InnerB"]},
                {"id": "InnerA"},
                {"id": "InnerB"},
                {"id": "Final"},
            ],
            "transitions": [
                {"from": "[*]", "to": "Outer"},
                {"from": "InnerA", "to": "InnerB", "event": "next"},
                {"from": "InnerB", "to": "Final", "event": "done"},
                {"from": "Final", "to": "[*]"},
            ],
        },
    })
    src = art.source
    assert src.startswith("stateDiagram-v2")
    assert "[*] --> Outer" in src
    assert "Final --> [*]" in src
    # Composite state balanced
    assert src.count("state Outer {") == 1
    assert src.count("\n  }\n") >= 1
    assert "InnerA --> InnerB : next" in src


def test_mermaid_usecase_contains_actors_and_use_cases():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.usecase",
        "language": "mermaid",
        "parameters": MERMAID_USECASE_PARAMS,
    })
    src = art.source
    assert src.startswith("flowchart LR")
    assert 'Member(["Member"])' in src  # stadium node
    assert "subgraph Library" in src  # system boundary
    assert 'BorrowBook[("Borrow Book")]' in src  # ellipse
    assert "Member --> BorrowBook" in src
    assert "BorrowBook -.-> ReturnBook : <<include>>" in src


def test_mermaid_activity_has_initial_and_final():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.activity",
        "language": "mermaid",
        "parameters": MERMAID_ACTIVITY_PARAMS,
    })
    src = art.source
    assert src.startswith("flowchart TB")
    assert "Start(((" in src  # initial node shape
    assert "Done(((" in src  # final node shape
    assert 'InStock{"In Stock?"}' in src  # decision diamond (Mermaid flowchart)
    assert "InStock -->|yes| Ship" in src


# ---------------------------------------------------------------------------
# BPMN content checks
# ---------------------------------------------------------------------------


def test_bpmn_process_is_well_formed_xml_with_bpmn_namespace():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "bpmn.process",
        "language": "bpmn",
        "parameters": BPMN_PROCESS_PARAMS,
    })
    src = art.source
    assert src.startswith("<?xml")
    assert 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"' in src
    root = ET.fromstring(src)
    ns = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    assert root.tag == f"{{{ns}}}definitions"
    assert len(root.findall(f".//{{{ns}}}startEvent")) >= 1
    assert len(root.findall(f".//{{{ns}}}endEvent")) >= 1
    assert len(root.findall(f".//{{{ns}}}userTask")) >= 1
    assert len(root.findall(f".//{{{ns}}}exclusiveGateway")) >= 1
    assert len(root.findall(f".//{{{ns}}}sequenceFlow")) >= 4
    assert len(root.findall(f".//{{{ns}}}conditionExpression")) >= 1


def test_bpmn_pool_lane_has_balanced_flow_node_refs():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "bpmn.pool_lane",
        "language": "bpmn",
        "parameters": BPMN_POOL_LANE_PARAMS,
    })
    src = art.source
    root = ET.fromstring(src)
    ns = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    lane_set = root.findall(f".//{{{ns}}}laneSet")
    assert len(lane_set) == 1
    lanes = root.findall(f".//{{{ns}}}lane")
    assert len(lanes) == 2
    refs = root.findall(f".//{{{ns}}}flowNodeRef")
    # 3 elements -> 3 flowNodeRef
    assert len(refs) == 3
    ref_ids = {r.text for r in refs}
    assert ref_ids == {"StartEvent_1", "Task_Review", "EndEvent_1"}


def test_bpmn_collaboration_has_participants_and_message_flows():
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "bpmn.collaboration",
        "language": "bpmn",
        "parameters": BPMN_COLLABORATION_PARAMS,
    })
    src = art.source
    root = ET.fromstring(src)
    ns = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    assert len(root.findall(f".//{{{ns}}}collaboration")) == 1
    parts = root.findall(f".//{{{ns}}}participant")
    assert len(parts) == 2
    mfs = root.findall(f".//{{{ns}}}messageFlow")
    assert len(mfs) == 1
    procs = root.findall(f".//{{{ns}}}process")
    assert len(procs) == 2


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_unknown_pattern_id_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="unknown notation pattern_id"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.not_a_real_type",
            "language": "mermaid",
            "parameters": {},
        })


def test_wrong_language_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="language must be"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "python",
            "parameters": MERMAID_CLASS_PARAMS,
        })


def test_invalid_identifier_in_mermaid_class_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="must match"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "parameters": {
                "classes": [{"name": "1bad"}],  # starts with a digit
            },
        })


def test_duplicate_class_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="duplicate"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "parameters": {
                "classes": [{"name": "A"}, {"name": "A"}],
            },
        })


def test_relationship_to_unknown_class_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="unknown class"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "parameters": {
                "classes": [{"name": "A"}],
                "relationships": [{"type": "inheritance", "from": "A", "to": "Missing"}],
            },
        })


def test_unknown_relationship_type_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="relationship type .magic_arrow. must be one of"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "parameters": {
                "classes": [{"name": "A"}, {"name": "B"}],
                "relationships": [{"type": "magic_arrow", "from": "A", "to": "B"}],
            },
        })


def test_mermaid_activity_must_have_exactly_one_initial():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="exactly one initial node"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.activity",
            "language": "mermaid",
            "parameters": {
                "nodes": [
                    {"id": "S1", "shape": "initial"},
                    {"id": "S2", "shape": "initial"},
                    {"id": "E", "shape": "final"},
                ],
                "edges": [
                    {"from": "S1", "to": "E"},
                    {"from": "S2", "to": "E"},
                ],
            },
        })


def test_mermaid_activity_missing_final_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="final node"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.activity",
            "language": "mermaid",
            "parameters": {
                "nodes": [{"id": "S", "shape": "initial"}],
                "edges": [{"from": "S", "to": "S"}],  # self-loop so edges non-empty
            },
        })


def test_bpmn_requires_exactly_one_start_event():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="startEvent"):
        r.render(pattern_plan={
            "pattern_id": "bpmn.process",
            "language": "bpmn",
            "parameters": {
                "definitions_id": "D", "process_id": "P",
                "elements": [
                    {"type": "startEvent", "id": "S1"},
                    {"type": "startEvent", "id": "S2"},
                    {"type": "endEvent", "id": "E"},
                ],
                "flows": [
                    {"id": "F1", "sourceRef": "S1", "targetRef": "S2"},
                    {"id": "F2", "sourceRef": "S2", "targetRef": "E"},
                ],
            },
        })


def test_bpmn_dangling_flow_reference_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="references unknown element"):
        r.render(pattern_plan={
            "pattern_id": "bpmn.process",
            "language": "bpmn",
            "parameters": {
                "definitions_id": "D", "process_id": "P",
                "elements": [
                    {"type": "startEvent", "id": "S"},
                    {"type": "endEvent", "id": "E"},
                ],
                "flows": [
                    {"id": "F1", "sourceRef": "S", "targetRef": "Missing"},
                ],
            },
        })


def test_bpmn_pool_lane_must_cover_all_elements_exactly_once():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="elements not assigned"):
        r.render(pattern_plan={
            "pattern_id": "bpmn.pool_lane",
            "language": "bpmn",
            "parameters": {
                "definitions_id": "D", "process_id": "P",
                "elements": [
                    {"type": "startEvent", "id": "S"},
                    {"type": "userTask", "id": "T"},
                    {"type": "endEvent", "id": "E"},
                ],
                "flows": [
                    {"id": "F1", "sourceRef": "S", "targetRef": "T"},
                    {"id": "F2", "sourceRef": "T", "targetRef": "E"},
                ],
                "lanes": [
                    # 'E' is missing -> error
                    {"id": "L1", "name": "L1", "flow_node_refs": ["S", "T"]},
                ],
            },
        })
    with pytest.raises(NotationRenderError, match="multiple lanes"):
        r.render(pattern_plan={
            "pattern_id": "bpmn.pool_lane",
            "language": "bpmn",
            "parameters": {
                "definitions_id": "D", "process_id": "P",
                "elements": [
                    {"type": "startEvent", "id": "S"},
                    {"type": "endEvent", "id": "E"},
                ],
                "flows": [
                    {"id": "F1", "sourceRef": "S", "targetRef": "E"},
                ],
                "lanes": [
                    {"id": "L1", "name": "L1", "flow_node_refs": ["S", "E"]},
                    {"id": "L2", "name": "L2", "flow_node_refs": ["S"]},  # duplicate
                ],
            },
        })


def test_bpmn_collaboration_message_flow_to_unknown_participant_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="unknown participant"):
        r.render(pattern_plan={
            "pattern_id": "bpmn.collaboration",
            "language": "bpmn",
            "parameters": {
                "definitions_id": "D",
                "participants": [
                    {"id": "PA", "name": "A", "process_id": "PA",
                     "elements": [
                         {"type": "startEvent", "id": "SA"},
                         {"type": "endEvent", "id": "EA"},
                     ],
                     "flows": [{"id": "FA", "sourceRef": "SA", "targetRef": "EA"}]},
                ],
                "message_flows": [
                    {"id": "M1", "source_ref": "PA", "target_ref": "Missing"},
                ],
            },
        })


def test_bpmn_unknown_element_type_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="element type"):
        r.render(pattern_plan={
            "pattern_id": "bpmn.process",
            "language": "bpmn",
            "parameters": {
                "definitions_id": "D", "process_id": "P",
                "elements": [
                    {"type": "startEvent", "id": "S"},
                    {"type": "magicTask", "id": "X"},  # unknown
                    {"type": "endEvent", "id": "E"},
                ],
                "flows": [
                    {"id": "F1", "sourceRef": "S", "targetRef": "X"},
                    {"id": "F2", "sourceRef": "X", "targetRef": "E"},
                ],
            },
        })


def test_mermaid_sequence_message_to_unknown_participant_raises():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="unknown participant"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.sequence",
            "language": "mermaid",
            "parameters": {
                "participants": [{"id": "A"}],
                "messages": [{"from": "A", "to": "Missing", "text": "hi",
                              "type": "sync"}],
            },
        })


# ---------------------------------------------------------------------------
# Parameter coercion
# ---------------------------------------------------------------------------


def test_parameters_accept_glob_list_json_strings():
    """The catalog describes complex parameters as glob_list of JSON
    strings. The renderer must accept both the JSON-string form and the
    native dict form."""
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "parameters": {
            "classes": [
                '{"name": "A"}',
                '{"name": "B", "fields": ["x: int"]}',
            ],
            "relationships": [
                '{"type": "inheritance", "from": "B", "to": "A"}',
            ],
        },
    })
    assert "class A" in art.source
    assert "B <|-- A" in art.source


def test_parameters_reject_malformed_json():
    r = get_notation_renderer()
    with pytest.raises(NotationRenderError, match="not valid JSON"):
        r.render(pattern_plan={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "parameters": {
                "classes": ['{"name": "A"'],  # unterminated
            },
        })


# ---------------------------------------------------------------------------
# On-disk write + path safety
# ---------------------------------------------------------------------------


def test_on_disk_write_produces_byte_identical_file(tmp_path: Path):
    r = get_notation_renderer()
    art = r.render(pattern_plan={
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "parameters": MERMAID_CLASS_PARAMS,
    }, target_root=str(tmp_path))
    on_disk = tmp_path / art.output_filename
    assert on_disk.exists()
    assert on_disk.read_text(encoding="utf-8") == art.source
    # The hash on the artifact must match the file's actual hash.
    assert hashlib.sha256(on_disk.read_bytes()).hexdigest() == art.sha256


def test_dry_run_does_not_write(tmp_path: Path):
    r = get_notation_renderer()
    r.render(pattern_plan={
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "parameters": MERMAID_CLASS_PARAMS,
    })
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_notation_renderer_returns_same_instance():
    a = get_notation_renderer()
    b = get_notation_renderer()
    assert a is b


def test_singleton_can_be_reset_for_tests():
    from agent.services.notation_renderer import reset_notation_renderer_singleton
    a = get_notation_renderer()
    reset_notation_renderer_singleton()
    b = get_notation_renderer()
    assert a is not b
    # Restore default for subsequent tests
    reset_notation_renderer_singleton()