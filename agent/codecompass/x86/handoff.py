"""X86CC-028: Worker context handoff for x86 CodeCompass.

Provides a stable shape that worker tools can consume: a function-centric
context package containing the function node, its instructions, edges,
warnings, and a schema id.

This module is intentionally small and side-effect-free so it can be used
in both worker-runtime and offline-analysis paths.
"""
from __future__ import annotations

from typing import Any

CONTEXT_SCHEMA = "codecompass_x86_context.v1"


def build_x86_worker_context(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    function_id: str,
) -> dict[str, Any]:
    """Build a worker-consumable x86 context package for one function.

    Returns a dict:
      {
        "schema": "codecompass_x86_context.v1",
        "function_id": "...",
        "status": "ok" | "error",
        "function": {...},
        "instructions": [...],
        "basic_blocks": [...],
        "calls": [...],
        "warnings": [...],
      }

    If the function_id is empty or not found, status is "error" and an empty
    package is returned.
    """
    fid = str(function_id or "").strip()
    if not fid:
        return {
            "schema": CONTEXT_SCHEMA,
            "status": "error",
            "error": "function_id_required",
            "function_id": "",
            "function": {},
            "instructions": [],
            "basic_blocks": [],
            "calls": [],
            "warnings": ["function_id_required"],
        }

    nodes_by_id: dict[str, dict[str, Any]] = {str(n.get("id") or ""): n for n in nodes if isinstance(n, dict)}
    fn_node = nodes_by_id.get(fid)
    if fn_node is None:
        return {
            "schema": CONTEXT_SCHEMA,
            "status": "error",
            "error": f"unknown_function_id:{fid}",
            "function_id": fid,
            "function": {},
            "instructions": [],
            "basic_blocks": [],
            "calls": [],
            "warnings": [f"unknown_function_id:{fid}"],
        }

    # Naive but correct membership: include all instructions whose address
    # falls inside the function's [start, end] range. Functions without end
    # are treated as containing every instruction that belongs to no other
    # function with a defined range.
    start = fn_node.get("address")
    end_attr = (fn_node.get("attributes") or {}).get("end_address")
    end = end_attr if isinstance(end_attr, int) else None

    fn_instructions: list[dict[str, Any]] = []
    for n in nodes:
        if n.get("kind") != "instruction":
            continue
        addr = n.get("address")
        if start is not None and isinstance(addr, int):
            if addr < start:
                continue
            if end is not None and addr >= end:
                continue
        fn_instructions.append(n)

    fn_bbs = [n for n in nodes if n.get("kind") == "basic_block" and (
        not isinstance(start, int) or (isinstance(n.get("address"), int) and n["address"] >= start)
    )]

    calls: list[dict[str, Any]] = []
    for e in edges:
        if e.get("source") == fid and e.get("edge_type") in {"calls", "indirect_calls", "references_import"}:
            calls.append(e)

    warnings: list[str] = []
    if end is None:
        warnings.append("function_end_address_missing")
    if any(e.get("edge_type") == "indirect_calls" for e in calls):
        warnings.append("indirect_calls_present")

    return {
        "schema": CONTEXT_SCHEMA,
        "status": "ok",
        "function_id": fid,
        "function": fn_node,
        "instructions": fn_instructions,
        "basic_blocks": fn_bbs,
        "calls": calls,
        "warnings": warnings,
    }