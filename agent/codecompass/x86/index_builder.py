"""X86CC-014, X86CC-016: Index builder for x86 CodeCompass records."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from agent.codecompass.x86.adapter import X86DisassemblerAdapter
from agent.codecompass.x86.diagnostics import INDEX_TRUNCATED, x86_diag_dict
from agent.codecompass.x86.input_taxonomy import X86InputRecord


@dataclass
class X86IndexLimits:
    max_files: int = 100
    max_bytes_per_binary: int = 10_485_760  # 10 MB
    max_instructions: int = 50_000
    max_functions: int = 5_000
    max_basic_blocks: int = 20_000
    max_strings: int = 10_000


def _stable_id(prefix: str, *parts: Any) -> str:
    """Generate deterministic SHA256-based ID."""
    key = f"{prefix}:" + ":".join(str(p) for p in parts)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _sort_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort nodes deterministically: by kind, then address, then id."""
    return sorted(nodes, key=lambda n: (n.get("kind", ""), n.get("address") or 0, n.get("id", "")))


def _sort_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort edges deterministically: by source, target, edge_type."""
    return sorted(edges, key=lambda e: (e.get("source", ""), e.get("target", ""), e.get("edge_type", "")))


def build_manifest(
    *,
    profile: str,
    adapter: str,
    input_counts: dict[str, int],
    instruction_counts: int,
    function_counts: int,
    diagnostic_counts: int,
    truncation_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an index manifest describing the build result."""
    return {
        "profile": profile,
        "adapter": adapter,
        "input_counts": dict(input_counts),
        "instruction_count": instruction_counts,
        "function_count": function_counts,
        "diagnostic_count": diagnostic_counts,
        "truncation_info": truncation_info or {},
    }


class X86IndexBuilder:
    """Builds x86 index records from adapter output."""

    def __init__(self, limits: X86IndexLimits | None = None) -> None:
        self._limits = limits or X86IndexLimits()

    def build(
        self,
        input_record: X86InputRecord,
        adapter: X86DisassemblerAdapter,
    ) -> dict[str, Any]:
        """Build index from input_record via adapter.

        Returns dict with:
          x86_nodes, x86_edges, x86_diagnostics, x86_manifest,
          graph_nodes, graph_edges
        """
        try:
            raw = adapter.disassemble(input_record)
        except Exception as exc:  # noqa: BLE001
            raw = {
                "nodes": [],
                "edges": [],
                "diagnostics": [x86_diag_dict("adapter_error", f"adapter raised: {exc}")],
                "metadata": {"adapter": adapter.name, "instruction_count": 0},
            }

        nodes = list(raw.get("nodes") or [])
        edges = list(raw.get("edges") or [])
        diags = list(raw.get("diagnostics") or [])
        meta = dict(raw.get("metadata") or {})

        # Enforce limits with deterministic truncation
        truncation_info: dict[str, Any] = {}
        limits = self._limits

        instr_nodes = [n for n in nodes if n.get("kind") == "instruction"]
        other_nodes = [n for n in nodes if n.get("kind") != "instruction"]

        if len(instr_nodes) > limits.max_instructions:
            truncation_info["instructions_truncated"] = len(instr_nodes) - limits.max_instructions
            instr_nodes = instr_nodes[: limits.max_instructions]
            diags.append(x86_diag_dict(
                INDEX_TRUNCATED,
                f"Instructions truncated to {limits.max_instructions} (prefer entry point / symbols)",
                severity="warning",
            ))

        fn_nodes = [n for n in other_nodes if n.get("kind") == "function"]
        if len(fn_nodes) > limits.max_functions:
            truncation_info["functions_truncated"] = len(fn_nodes) - limits.max_functions
            fn_nodes = fn_nodes[: limits.max_functions]
            diags.append(x86_diag_dict(INDEX_TRUNCATED, f"Functions truncated to {limits.max_functions}", severity="warning"))

        str_nodes = [n for n in other_nodes if n.get("kind") == "string_literal"]
        if len(str_nodes) > limits.max_strings:
            truncation_info["strings_truncated"] = len(str_nodes) - limits.max_strings
            str_nodes = str_nodes[: limits.max_strings]
            diags.append(x86_diag_dict(INDEX_TRUNCATED, f"Strings truncated to {limits.max_strings}", severity="warning"))

        non_special = [n for n in other_nodes if n.get("kind") not in {"function", "string_literal"}]
        all_nodes = _sort_nodes(non_special + fn_nodes + instr_nodes + str_nodes)
        all_edges = _sort_edges(edges)

        # Annotate with provenance
        for n in all_nodes:
            n.setdefault("_provenance", {})
            n["_provenance"].update({
                "output_kind": "x86_nodes",
                "adapter": adapter.name,
                "profile": input_record.abi,
                "source_input": input_record.kind,
            })

        for e in all_edges:
            e.setdefault("_provenance", {})
            e["_provenance"].update({
                "output_kind": "x86_edges",
                "adapter": adapter.name,
            })

        profile = str(meta.get("profile") or input_record.abi or "unknown_x86")
        manifest = build_manifest(
            profile=profile,
            adapter=adapter.name,
            input_counts={"nodes": len(nodes), "edges": len(edges)},
            instruction_counts=meta.get("instruction_count", len(instr_nodes)),
            function_counts=meta.get("function_count", len(fn_nodes)),
            diagnostic_counts=len(diags),
            truncation_info=truncation_info,
        )

        return {
            "x86_nodes": all_nodes,
            "x86_edges": all_edges,
            "x86_diagnostics": diags,
            "x86_manifest": manifest,
            "graph_nodes": all_nodes,  # same set for now; pipeline may merge
            "graph_edges": all_edges,
        }
