"""X86CC-023..027: x86 CodeCompass tools for the ananta-worker tool loop."""

from __future__ import annotations

from typing import Any

from agent.services.tools._evidence import (
    build_evidence_entry,
    build_tool_result,
)
from agent.services.tools.codecompass_graph_store_access import (
    GraphStoreResolver,
    resolve_graph_store,
)

# ===========================================================================
# X86CC-023..027: x86 CodeCompass tools
# ===========================================================================
#
# These five tools expose the x86 extension's graph-store data through the
# same shape as the existing codecompass_* tools: workspace_dir, arguments,
# tool_call_id -> ToolResult. They are feature-flag-gated through the x86
# graph-store payload (`x86_extension.status`); if no x86 records exist,
# they degrade to a clean OK result with empty data, never an exception.

_X86_EVIDENCE_KIND_INSTRUCTION = "x86_instruction"
_X86_EVIDENCE_KIND_BASIC_BLOCK = "x86_basic_block"
_X86_EVIDENCE_KIND_FUNCTION = "x86_function"
_X86_EVIDENCE_KIND_CFG_EDGE = "x86_cfg_edge"
_X86_EVIDENCE_KIND_CALLSITE = "x86_callsite"


def _x86_payload(
    arguments: dict[str, Any],
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
):
    """Resolve the x86 extension payload (nodes/edges/index) from the graph store.

    Returns (x86_index_dict, store_or_none). The store is exposed for tests that
    may want to assert against the same store reference.
    """
    store = resolve_graph_store(arguments)
    if store is None:
        return ({
            "schema": "codecompass_x86_graph.v1",
            "nodes": [], "edges": [], "nodes_by_id": {},
            "node_count": 0, "edge_count": 0,
        }, None)
    payload = store.load()
    x86_index = dict(payload.get("x86_index") or {})
    if not x86_index:
        x86_index = {
            "schema": "codecompass_x86_graph.v1",
            "nodes": [], "edges": [], "nodes_by_id": {},
            "node_count": 0, "edge_count": 0,
        }
    return (x86_index, store)


def _x86_kind_evidence_kind(node_kind: str) -> str:
    return {
        "instruction": _X86_EVIDENCE_KIND_INSTRUCTION,
        "basic_block": _X86_EVIDENCE_KIND_BASIC_BLOCK,
        "function": _X86_EVIDENCE_KIND_FUNCTION,
        "callsite": _X86_EVIDENCE_KIND_CALLSITE,
    }.get(str(node_kind or ""), "x86_diagnostic")


# ----- X86CC-023: x86_overview -----

def codecompass_x86_overview(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
) -> dict[str, Any]:
    """Summarize the x86 extension payload: counts, kinds present, diagnostics.

    Returns ToolResult with data.summary = {node_count, edge_count, by_kind, by_section}.
    """
    x86_index, _ = _x86_payload(arguments, resolve_graph_store)
    nodes = list(x86_index.get("nodes") or [])
    edges = list(x86_index.get("edges") or [])
    by_kind: dict[str, int] = {}
    by_section: dict[str, int] = {}
    for n in nodes:
        kind = str(n.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        attrs = n.get("attributes") or {}
        section = str(attrs.get("name") or n.get("section") or "")
        if section:
            by_section[section] = by_section.get(section, 0) + 1
    summary = {
        "schema": x86_index.get("schema", "codecompass_x86_graph.v1"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": dict(sorted(by_kind.items())),
        "by_section": dict(sorted(by_section.items())),
    }
    return build_tool_result(
        tool_name="codecompass.x86_overview",
        tool_call_id=tool_call_id,
        status="ok",
        data={"summary": summary},
    )


# ----- X86CC-024: x86_address_lookup -----

def codecompass_x86_address_lookup(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
) -> dict[str, Any]:
    """Resolve a single address (hex 0x... or decimal) to x86 nodes."""
    args = arguments or {}
    raw_addr = str(args.get("address") or "").strip()
    if not raw_addr:
        return build_tool_result(
            tool_name="codecompass.x86_address_lookup",
            tool_call_id=tool_call_id,
            status="error",
            error="address_required",
        )
    from agent.codecompass.x86.graph_extensions import build_x86_index
    from agent.codecompass.x86.query import X86Query, X86QueryEngine, parse_address

    addr = parse_address(raw_addr)
    if addr is None:
        return build_tool_result(
            tool_name="codecompass.x86_address_lookup",
            tool_call_id=tool_call_id,
            status="error",
            error=f"invalid_address:{raw_addr!r}",
        )

    x86_index, _ = _x86_payload(arguments, resolve_graph_store)
    nodes = list(x86_index.get("nodes") or [])
    idx = build_x86_index(nodes)
    nodes_by_id = dict(x86_index.get("nodes_by_id") or {})
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="address", value=raw_addr, limit=20), idx)
    matched = [nodes_by_id[nid] for nid in result.node_ids if nid in nodes_by_id]
    evidence = [
        build_evidence_entry(
            kind=_x86_kind_evidence_kind(n.get("kind", "")),
            path=str(n.get("id") or ""),
            excerpt=str(n.get("attributes") or {}).replace("\n", " ")[:200],
        )[0]  # take the entry, drop the truncation flag
        for n in matched[:5]
    ]
    return build_tool_result(
        tool_name="codecompass.x86_address_lookup",
        tool_call_id=tool_call_id,
        status="ok",
        data={
            "address": raw_addr,
            "resolved": addr,
            "nodes": matched,
            "warnings": result.warnings,
        },
        evidence=evidence,
    )


# ----- X86CC-025: x86_cfg -----

def codecompass_x86_cfg(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
) -> dict[str, Any]:
    """CFG traversal (cycle-safe, bounded) from a seed node id."""
    args = arguments or {}
    seed = str(args.get("seed_id") or "").strip()
    if not seed:
        return build_tool_result(
            tool_name="codecompass.x86_cfg",
            tool_call_id=tool_call_id,
            status="error",
            error="seed_id_required",
        )
    from agent.codecompass.x86.graph_extensions import X86CFGTraversal, build_x86_index

    x86_index, _ = _x86_payload(arguments, resolve_graph_store)
    nodes = list(x86_index.get("nodes") or [])
    edges = list(x86_index.get("edges") or [])
    nodes_by_id = dict(x86_index.get("nodes_by_id") or {})
    if seed not in nodes_by_id:
        return build_tool_result(
            tool_name="codecompass.x86_cfg",
            tool_call_id=tool_call_id,
            status="error",
            error=f"unknown_seed_id:{seed!r}",
        )
    edges_by_source: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        edges_by_source.setdefault(str(e.get("source") or ""), []).append(e)
    idx = build_x86_index(nodes)
    result = X86CFGTraversal().traverse(
        idx, nodes_by_id, edges_by_source, seed,
        max_depth=int(args.get("max_depth", 20)),
        max_nodes=int(args.get("max_nodes", 200)),
    )
    return build_tool_result(
        tool_name="codecompass.x86_cfg",
        tool_call_id=tool_call_id,
        status="ok",
        data={
            "seed_id": seed,
            "nodes": result["nodes"],
            "edges": result["edges"],
            "warnings": result["warnings"],
        },
    )


# ----- X86CC-026: x86_call_graph -----

def codecompass_x86_call_graph(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
) -> dict[str, Any]:
    """Call-graph traversal: classify calls into direct/indirect/import/unresolved."""
    args = arguments or {}
    seed = str(args.get("seed_id") or "").strip()
    if not seed:
        return build_tool_result(
            tool_name="codecompass.x86_call_graph",
            tool_call_id=tool_call_id,
            status="error",
            error="seed_id_required",
        )
    from agent.codecompass.x86.graph_extensions import X86CallGraphTraversal

    x86_index, _ = _x86_payload(arguments, resolve_graph_store)
    nodes_by_id = dict(x86_index.get("nodes_by_id") or {})
    edges = list(x86_index.get("edges") or [])
    if seed not in nodes_by_id:
        return build_tool_result(
            tool_name="codecompass.x86_call_graph",
            tool_call_id=tool_call_id,
            status="error",
            error=f"unknown_seed_id:{seed!r}",
        )
    result = X86CallGraphTraversal().traverse(
        nodes_by_id, edges, seed,
        max_depth=int(args.get("max_depth", 10)),
        max_nodes=int(args.get("max_nodes", 100)),
    )
    return build_tool_result(
        tool_name="codecompass.x86_call_graph",
        tool_call_id=tool_call_id,
        status="ok",
        data={
            "seed_id": seed,
            "nodes": result["nodes"],
            "edges": result["edges"],
            "direct_calls": result["direct_calls"],
            "indirect_calls": result["indirect_calls"],
            "import_calls": result["import_calls"],
            "unresolved": result["unresolved"],
            "warnings": result["warnings"],
        },
    )


# ----- X86CC-027: x86_find -----

def codecompass_x86_find(
    *, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str,
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
) -> dict[str, Any]:
    """Generic x86 finder: address/symbol/function/mnemonic/import/string/section/basic_block."""
    args = arguments or {}
    kind = str(args.get("kind") or "").strip()
    value = str(args.get("value") or "").strip()
    if not kind:
        return build_tool_result(
            tool_name="codecompass.x86_find",
            tool_call_id=tool_call_id,
            status="error",
            error="kind_required",
        )
    from agent.codecompass.x86.graph_extensions import build_x86_index
    from agent.codecompass.x86.query import VALID_QUERY_KINDS, X86Query, X86QueryEngine

    if kind not in VALID_QUERY_KINDS:
        return build_tool_result(
            tool_name="codecompass.x86_find",
            tool_call_id=tool_call_id,
            status="error",
            error=f"invalid_kind:{kind}",
        )

    x86_index, _ = _x86_payload(arguments, resolve_graph_store)
    nodes = list(x86_index.get("nodes") or [])
    nodes_by_id = dict(x86_index.get("nodes_by_id") or {})
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    limit = int(args.get("limit", 50))
    result = engine.execute(X86Query(kind=kind, value=value, limit=limit), idx)
    matched = [nodes_by_id[nid] for nid in result.node_ids if nid in nodes_by_id]
    return build_tool_result(
        tool_name="codecompass.x86_find",
        tool_call_id=tool_call_id,
        status="ok" if result.status == "ok" else "error",
        data={
            "kind": kind,
            "value": value,
            "nodes": matched,
            "warnings": result.warnings,
        },
        error=result.error,
    )
