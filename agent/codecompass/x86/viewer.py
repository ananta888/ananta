"""X86CC-029: Viewer model for x86 assembly analysis output."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class X86ViewerView:
    """A single view (function_overview / cfg_view / call_graph_view / section_map)."""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    address_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncation_flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "labels": self.labels,
            "address_refs": self.address_refs,
            "warnings": self.warnings,
            "truncation_flags": self.truncation_flags,
        }


@dataclass
class X86ViewerModel:
    """Top-level viewer model with multiple views."""
    function_overview: X86ViewerView = field(default_factory=X86ViewerView)
    cfg_view: X86ViewerView = field(default_factory=X86ViewerView)
    call_graph_view: X86ViewerView = field(default_factory=X86ViewerView)
    section_map: X86ViewerView = field(default_factory=X86ViewerView)

    def as_dict(self) -> dict[str, Any]:
        return {
            "function_overview": self.function_overview.as_dict(),
            "cfg_view": self.cfg_view.as_dict(),
            "call_graph_view": self.call_graph_view.as_dict(),
            "section_map": self.section_map.as_dict(),
        }


def _address_ref(node: dict[str, Any]) -> dict[str, Any]:
    addr = node.get("address")
    return {
        "kind": "absolute_address",
        "value": addr,
        "label": f"0x{addr:x}" if isinstance(addr, int) else "unknown",
        "section": (node.get("attributes") or {}).get("section", ""),
    }


def build_function_overview(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    limits: dict[str, int] | None = None,
) -> X86ViewerModel:
    """Build an X86ViewerModel from raw node and edge lists."""
    _limits = limits or {}
    max_fn = _limits.get("max_functions", 200)
    max_instr = _limits.get("max_instructions", 500)

    model = X86ViewerModel()
    warnings: list[str] = []
    truncation_flags: list[str] = []

    fn_nodes = [n for n in nodes if n.get("kind") == "function"]
    section_nodes = [n for n in nodes if n.get("kind") == "section"]
    bb_nodes = [n for n in nodes if n.get("kind") == "basic_block"]
    instr_nodes = [n for n in nodes if n.get("kind") == "instruction"]
    import_nodes = [n for n in nodes if n.get("kind") == "import_symbol"]

    if len(fn_nodes) > max_fn:
        truncation_flags.append("functions_truncated")
        fn_nodes = fn_nodes[:max_fn]
    if len(instr_nodes) > max_instr:
        truncation_flags.append("instructions_truncated")
        instr_nodes = instr_nodes[:max_instr]

    # Function overview
    model.function_overview = X86ViewerView(
        nodes=fn_nodes + import_nodes,
        edges=[e for e in edges if e.get("edge_type") in {"calls", "indirect_calls", "references_import"}],
        labels={
            str(n.get("id", "")): (n.get("attributes") or {}).get("name", str(n.get("id", "")))
            for n in fn_nodes
        },
        address_refs=[_address_ref(n) for n in fn_nodes],
        warnings=warnings,
        truncation_flags=list(truncation_flags),
    )

    # CFG view
    cfg_edge_types = {"cfg_fallthrough", "cfg_true", "cfg_false", "cfg_jump", "cfg_indirect_jump"}
    cfg_edges = [e for e in edges if e.get("edge_type") in cfg_edge_types]
    if any(e.get("edge_type") == "cfg_indirect_jump" for e in cfg_edges):
        model.cfg_view.warnings.append("cfg_incomplete:indirect_jump")

    model.cfg_view = X86ViewerView(
        nodes=bb_nodes + fn_nodes,
        edges=cfg_edges,
        labels={str(n.get("id", "")): f"bb@{n.get('address', 0):x}" if isinstance(n.get("address"), int) else str(n.get("id", "")) for n in bb_nodes},
        address_refs=[_address_ref(n) for n in bb_nodes],
        warnings=["cfg_incomplete:indirect_jump"] if any(e.get("edge_type") == "cfg_indirect_jump" for e in cfg_edges) else [],
        truncation_flags=list(truncation_flags),
    )

    # Section map
    model.section_map = X86ViewerView(
        nodes=section_nodes,
        edges=[],
        labels={str(n.get("id", "")): (n.get("attributes") or {}).get("name", "") for n in section_nodes},
        address_refs=[_address_ref(n) for n in section_nodes],
        warnings=[],
        truncation_flags=[],
    )

    # Call graph view
    call_edges = [e for e in edges if e.get("edge_type") in {"calls", "indirect_calls"}]
    model.call_graph_view = X86ViewerView(
        nodes=fn_nodes + import_nodes,
        edges=call_edges,
        labels={
            str(n.get("id", "")): (n.get("attributes") or {}).get("name", str(n.get("id", "")))
            for n in fn_nodes + import_nodes
        },
        address_refs=[_address_ref(n) for n in fn_nodes],
        warnings=[],
        truncation_flags=list(truncation_flags),
    )

    return model
