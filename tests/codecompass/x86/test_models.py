"""Contract tests for W2 (X86CC-006..010): node kinds, edge types, address/location refs, evidence kinds, provenance + confidence.

All five sub-tasks share agent/codecompass/x86/models.py. One test file because
the data shapes are tightly coupled and a single regression in one breaks the others.
"""

from __future__ import annotations

import json

import pytest

from agent.codecompass.x86.models import (
    AddressKind,
    AddressRef,
    ALL_ADDRESS_KINDS,
    ALL_EDGE_TYPES,
    ALL_EVIDENCE_KINDS,
    ALL_NODE_KINDS,
    LocationRef,
    SCHEMA_ID,
    X86Edge,
    X86EvidenceKind,
    X86Node,
    X86NodeKind,
    X86Provenance,
)


# ===== X86CC-006: Node kinds =====

def test_node_kinds_cover_all_required_shapes():
    expected = {
        "binary_file", "section", "segment", "symbol", "function", "basic_block",
        "instruction", "operand", "register", "flag", "stack_slot", "memory_region",
        "callsite", "import_symbol", "export_symbol", "relocation", "string_literal",
        "address_range",
    }
    assert ALL_NODE_KINDS == expected
    assert len(ALL_NODE_KINDS) == 18


def test_x86_node_as_record_has_required_fields():
    node = X86Node(
        id="node-1", kind=X86NodeKind.INSTRUCTION, source_type="disassembler_json",
        address=0x401014, confidence=0.95,
        provenance={"adapter": "fixture"},
        attributes={"mnemonic": "mov"},
    )
    rec = node.as_record()
    assert rec["schema"] == SCHEMA_ID
    assert rec["id"] == "node-1"
    assert rec["kind"] == "instruction"
    assert rec["address"] == 0x401014
    assert rec["confidence"] == 0.95
    assert rec["provenance"] == {"adapter": "fixture"}
    assert rec["_provenance"] == {"output_kind": "x86_nodes"}


def test_x86_node_is_json_serializable():
    node = X86Node(id="n1", kind="instruction", source_type="fixture")
    blob = json.dumps(node.as_record())
    assert "x86_nodes" in blob


# ===== X86CC-007: Edge types =====

def test_edge_types_cover_all_required_shapes():
    expected = {
        "contains", "belongs_to", "next_instruction", "cfg_fallthrough",
        "cfg_true", "cfg_false", "cfg_jump", "cfg_indirect_jump",
        "calls", "indirect_calls", "returns_to",
        "reads_register", "writes_register", "reads_flag", "writes_flag",
        "reads_memory", "writes_memory", "uses_stack_slot",
        "references_string", "references_import", "references_address", "relocates_to",
    }
    assert ALL_EDGE_TYPES == expected
    assert len(ALL_EDGE_TYPES) == 22


def test_x86_edge_as_record_has_required_fields():
    edge = X86Edge(
        source_id="bb-1", target_id="bb-2", edge_type="cfg_fallthrough",
        confidence=1.0, address=0x401014,
        context={"reason": "fallthrough after ret-not-taken"},
    )
    rec = edge.as_record()
    assert rec["source"] == "bb-1"
    assert rec["target"] == "bb-2"
    assert rec["edge_type"] == "cfg_fallthrough"
    assert rec["address"] == 0x401014
    assert rec["context"]["reason"] == "fallthrough after ret-not-taken"
    assert rec["_provenance"] == {"output_kind": "x86_edges"}


# ===== X86CC-008: AddressRef + LocationRef =====

def test_address_kinds_complete():
    assert ALL_ADDRESS_KINDS == {
        "absolute_address", "relative_address", "file_offset", "rva", "unknown",
    }


def test_address_ref_roundtrip():
    addr = AddressRef(
        kind=AddressKind.ABSOLUTE, value=0x401014, label="main+0x14",
        section=".text", base_address=0x400000,
    )
    d = addr.as_dict()
    assert d["kind"] == "absolute_address"
    assert d["value"] == 0x401014
    assert d["label"] == "main+0x14"
    assert d["section"] == ".text"
    assert d["base_address"] == 0x400000


def test_address_ref_label_falls_back_to_hex():
    addr = AddressRef(kind=AddressKind.ABSOLUTE, value=0x401014)
    assert addr.as_dict()["label"] == "0x401014"


def test_address_ref_unknown_value_label():
    addr = AddressRef(kind=AddressKind.UNKNOWN, value=None)
    assert addr.as_dict()["label"] == "unknown"


def test_location_ref_evidence_excerpt_prefers_symbol():
    loc = LocationRef(
        address_ref=AddressRef(kind=AddressKind.ABSOLUTE, value=0x401014, label="main+0x14"),
        symbol="main", section=".text",
    )
    excerpt = loc.as_evidence_excerpt()
    assert "main" in excerpt
    assert "main+0x14" in excerpt
    assert ".text" in excerpt


def test_location_ref_evidence_excerpt_falls_back_to_hex():
    loc = LocationRef(
        address_ref=AddressRef(kind=AddressKind.ABSOLUTE, value=0x401014),
    )
    excerpt = loc.as_evidence_excerpt()
    assert "0x401014" in excerpt


def test_location_ref_unknown_returns_placeholder():
    loc = LocationRef(address_ref=AddressRef(kind=AddressKind.UNKNOWN, value=None))
    assert loc.as_evidence_excerpt() == "unknown_location"


# ===== X86CC-009: Evidence kinds =====

def test_evidence_kinds_complete():
    expected = {
        "x86_instruction", "x86_basic_block", "x86_function",
        "x86_cfg_edge", "x86_callsite", "x86_section",
        "x86_import", "x86_string", "x86_diagnostic",
    }
    assert ALL_EVIDENCE_KINDS == expected
    assert len(ALL_EVIDENCE_KINDS) == 9


def test_evidence_kinds_namespaced():
    """All x86 evidence kinds must be prefixed 'x86_' so they don't collide with existing kinds."""
    for kind in ALL_EVIDENCE_KINDS:
        assert kind.startswith("x86_"), f"evidence kind {kind!r} missing x86_ prefix"


# ===== X86CC-010: Provenance + Confidence =====

def test_provenance_default_fields_present():
    p = X86Provenance()
    d = p.as_dict()
    assert d == {
        "source_file": "",
        "adapter": "",
        "adapter_version": "",
        "disassembler": "",
        "profile": "",
        "heuristic": False,
        "symbol_table": False,
        "debug_info": False,
        "manual_fixture": False,
    }


def test_provenance_heuristic_flag():
    p = X86Provenance(heuristic=True, source_file="/x", adapter="fixture")
    d = p.as_dict()
    assert d["heuristic"] is True
    assert d["source_file"] == "/x"


def test_node_confidence_in_record():
    node = X86Node(id="n", kind="instruction", source_type="fixture", confidence=0.42)
    assert node.as_record()["confidence"] == 0.42


def test_edge_confidence_in_record():
    edge = X86Edge(source_id="a", target_id="b", edge_type="calls", confidence=0.7)
    assert edge.as_record()["confidence"] == 0.7


def test_schema_id_is_stable():
    """Pin SCHEMA_ID — downstream graph files reference it; renaming breaks old graphs."""
    assert SCHEMA_ID == "codecompass_x86_graph.v1"