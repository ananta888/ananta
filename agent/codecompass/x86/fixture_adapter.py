"""X86CC-012: Fixture-based adapter reading JSON fixture files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.codecompass.x86.adapter import X86DisassemblerAdapter
from agent.codecompass.x86.diagnostics import (
    ADAPTER_ERROR,
    INVALID_INSTRUCTION_RECORD,
    x86_diag_dict,
)
from agent.codecompass.x86.input_taxonomy import X86InputRecord
from agent.codecompass.x86.models import (
    X86EdgeType,
    X86NodeKind,
    X86Provenance,
)

_FIXTURE_INPUT_TYPES = frozenset({
    "disassembler_json",
    "capstone_fixture",
    "ghidra_export",
    "rizin_export",
    "raw_assembly_text",
    "normalized_assembly",
})

_FIXTURE_PROFILES = frozenset({
    "x86_64_sysv",
    "x86_64_windows",
    "x86_32_cdecl",
    "x86_32_stdcall",
    "unknown_x86",
})


def load_fixture(path: str | Path) -> dict[str, Any]:
    """Load a JSON fixture file, returning its content dict or raising ValueError."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in fixture {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Fixture must be a JSON object, got {type(data).__name__}")
    return data


def _node_id(prefix: str, value: Any) -> str:
    """Generate a deterministic node ID via SHA256 of a stable string."""
    key = f"{prefix}:{value}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _build_nodes_from_fixture(data: dict[str, Any], profile: str, source_path: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    prov = X86Provenance(
        source_file=source_path,
        adapter="fixture_adapter",
        adapter_version="1.0",
        profile=profile,
        manual_fixture=True,
    ).as_dict()

    # Section nodes
    for sec in data.get("sections", []):
        if not isinstance(sec, dict):
            continue
        name = str(sec.get("name") or "unknown_section")
        nid = _node_id("section", f"{source_path}:{name}")
        nodes.append({
            "schema": "codecompass_x86_graph.v1",
            "id": nid,
            "kind": X86NodeKind.SECTION,
            "source_type": "fixture",
            "path": source_path,
            "address": sec.get("start"),
            "offset": None,
            "architecture_profile": profile,
            "confidence": 1.0,
            "provenance": prov,
            "attributes": {
                "name": name,
                "size": sec.get("size", 0),
                "flags": sec.get("flags", ""),
                "entropy": sec.get("entropy"),
            },
            "_provenance": {"output_kind": "x86_nodes"},
        })

    # Symbol nodes
    for sym in data.get("symbols", []):
        if not isinstance(sym, dict):
            continue
        name = str(sym.get("name") or "unknown_symbol")
        nid = _node_id("symbol", f"{source_path}:{name}:{sym.get('address')}")
        nodes.append({
            "schema": "codecompass_x86_graph.v1",
            "id": nid,
            "kind": X86NodeKind.SYMBOL,
            "source_type": "fixture",
            "path": source_path,
            "address": sym.get("address"),
            "offset": None,
            "architecture_profile": profile,
            "confidence": 1.0,
            "provenance": prov,
            "attributes": {
                "name": name,
                "kind": sym.get("kind", "unknown"),
                "size": sym.get("size", 0),
            },
            "_provenance": {"output_kind": "x86_nodes"},
        })

    # Import nodes
    for imp in data.get("imports", []):
        if not isinstance(imp, dict):
            continue
        imp_name = str(imp.get("name") or "unknown_import")
        dll = str(imp.get("dll") or "")
        nid = _node_id("import", f"{source_path}:{dll}:{imp_name}")
        nodes.append({
            "schema": "codecompass_x86_graph.v1",
            "id": nid,
            "kind": X86NodeKind.IMPORT_SYMBOL,
            "source_type": "fixture",
            "path": source_path,
            "address": imp.get("address"),
            "offset": None,
            "architecture_profile": profile,
            "confidence": 1.0,
            "provenance": prov,
            "attributes": {
                "name": imp_name,
                "dll": dll,
            },
            "_provenance": {"output_kind": "x86_nodes"},
        })

    # Function nodes
    for fn in data.get("functions", []):
        if not isinstance(fn, dict):
            continue
        fn_id = str(fn.get("id") or _node_id("function", f"{source_path}:{fn.get('name')}:{fn.get('start_address')}"))
        nodes.append({
            "schema": "codecompass_x86_graph.v1",
            "id": fn_id,
            "kind": X86NodeKind.FUNCTION,
            "source_type": "fixture",
            "path": source_path,
            "address": fn.get("start_address"),
            "offset": None,
            "architecture_profile": profile,
            "confidence": 1.0,
            "provenance": prov,
            "attributes": {
                "name": fn.get("name", ""),
                "end_address": fn.get("end_address"),
                "basic_blocks": fn.get("basic_blocks", []),
            },
            "_provenance": {"output_kind": "x86_nodes"},
        })

    # Instruction nodes
    for instr in data.get("instructions", []):
        if not isinstance(instr, dict):
            continue
        addr = instr.get("address")
        if addr is None:
            continue
        nid = _node_id("instruction", f"{source_path}:{addr}")
        nodes.append({
            "schema": "codecompass_x86_graph.v1",
            "id": nid,
            "kind": X86NodeKind.INSTRUCTION,
            "source_type": "fixture",
            "path": source_path,
            "address": addr,
            "offset": None,
            "architecture_profile": profile,
            "confidence": 1.0,
            "provenance": prov,
            "attributes": {
                "mnemonic": instr.get("mnemonic", ""),
                "bytes_hex": instr.get("bytes_hex", ""),
                "operands": instr.get("operands", []),
                "flag_effects": instr.get("flag_effects", []),
                "implicit_registers_read": instr.get("implicit_registers_read", []),
                "implicit_registers_written": instr.get("implicit_registers_written", []),
                "width": instr.get("width", 64),
            },
            "_provenance": {"output_kind": "x86_nodes"},
        })

    # String literal nodes
    for s in data.get("strings", []):
        if not isinstance(s, dict):
            continue
        val = str(s.get("value") or "")
        addr = s.get("address", 0)
        nid = _node_id("string", f"{source_path}:{addr}:{val[:32]}")
        nodes.append({
            "schema": "codecompass_x86_graph.v1",
            "id": nid,
            "kind": X86NodeKind.STRING_LITERAL,
            "source_type": "fixture",
            "path": source_path,
            "address": addr,
            "offset": None,
            "architecture_profile": profile,
            "confidence": 1.0,
            "provenance": prov,
            "attributes": {
                "value": val,
                "encoding": s.get("encoding", "ascii"),
                "section": s.get("section", ""),
                "length": s.get("length", len(val)),
            },
            "_provenance": {"output_kind": "x86_nodes"},
        })

    return nodes


def _build_edges_from_fixture(data: dict[str, Any], source_path: str, profile: str) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    prov = {"source_file": source_path, "adapter": "fixture_adapter"}

    for bb in data.get("basic_blocks", []):
        if not isinstance(bb, dict):
            continue
        bb_id = str(bb.get("id") or "")
        for succ in bb.get("successors", []):
            if not isinstance(succ, dict):
                continue
            edge_type_raw = str(succ.get("edge_type", "fallthrough"))
            # Map fixture edge_type to X86EdgeType
            edge_type_map = {
                "fallthrough": X86EdgeType.CFG_FALLTHROUGH,
                "conditional_true": X86EdgeType.CFG_TRUE,
                "conditional_false": X86EdgeType.CFG_FALSE,
                "direct_jump": X86EdgeType.CFG_JUMP,
                "indirect_jump": X86EdgeType.CFG_INDIRECT_JUMP,
                "call": X86EdgeType.CALLS,
                "return": X86EdgeType.RETURNS_TO,
            }
            etype = edge_type_map.get(edge_type_raw, X86EdgeType.CFG_FALLTHROUGH)
            tgt_addr = succ.get("address")
            tgt_id = _node_id("basic_block", f"{source_path}:{tgt_addr}") if tgt_addr else "unknown"
            edges.append({
                "schema": "codecompass_x86_graph.v1",
                "source": bb_id,
                "target": tgt_id,
                "edge_type": etype,
                "confidence": 1.0,
                "provenance": prov,
                "address": tgt_addr,
                "context": {},
                "_provenance": {"output_kind": "x86_edges"},
            })

    return edges


class FixtureAdapter(X86DisassemblerAdapter):
    """Adapter that reads JSON fixture files and produces node/edge records."""

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        self._fixture_path = Path(fixture_path) if fixture_path else None

    @property
    def name(self) -> str:
        return "fixture_adapter"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def supported_input_types(self) -> frozenset[str]:
        return _FIXTURE_INPUT_TYPES

    @property
    def supported_profiles(self) -> frozenset[str]:
        return _FIXTURE_PROFILES

    def disassemble(self, input_record: X86InputRecord) -> dict[str, Any]:
        path = self._fixture_path or (Path(input_record.fixture_path) if input_record.fixture_path else None)
        if path is None:
            return {
                "nodes": [],
                "edges": [],
                "diagnostics": [x86_diag_dict(ADAPTER_ERROR, "fixture_path not set")],
                "metadata": {"adapter": self.name, "instruction_count": 0},
            }

        try:
            data = load_fixture(path)
        except (FileNotFoundError, ValueError) as exc:
            return {
                "nodes": [],
                "edges": [],
                "diagnostics": [x86_diag_dict(ADAPTER_ERROR, str(exc))],
                "metadata": {"adapter": self.name, "instruction_count": 0},
            }

        meta = data.get("metadata", {})
        profile = str(meta.get("abi") or input_record.abi or "unknown_x86")
        source_path = str(path)

        diags_raw = data.get("diagnostics", [])
        diags = []
        for d in diags_raw:
            if isinstance(d, dict):
                diags.append(d)

        nodes = _build_nodes_from_fixture(data, profile, source_path)
        edges = _build_edges_from_fixture(data, source_path, profile)

        instructions = data.get("instructions", [])
        functions = data.get("functions", [])

        return {
            "nodes": nodes,
            "edges": edges,
            "diagnostics": diags,
            "metadata": {
                "profile": profile,
                "adapter": self.name,
                "adapter_version": self.version,
                "instruction_count": len(instructions),
                "function_count": len(functions),
                "basic_block_count": len(data.get("basic_blocks", [])),
                "section_count": len(data.get("sections", [])),
                "import_count": len(data.get("imports", [])),
                "string_count": len(data.get("strings", [])),
                "source_path": source_path,
                "architecture": meta.get("architecture", "x86_64"),
                "bitness": meta.get("bitness", 64),
            },
        }
