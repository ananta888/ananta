"""X86CC-017, X86CC-018: Graph index and traversal extensions for x86."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class X86GraphIndex:
    by_address: dict[str, list[str]] = field(default_factory=dict)
    by_file_offset: dict[str, list[str]] = field(default_factory=dict)
    by_section: dict[str, list[str]] = field(default_factory=dict)
    by_function: dict[str, list[str]] = field(default_factory=dict)
    by_basic_block: dict[str, list[str]] = field(default_factory=dict)
    by_symbol: dict[str, list[str]] = field(default_factory=dict)
    by_mnemonic: dict[str, list[str]] = field(default_factory=dict)
    by_import: dict[str, list[str]] = field(default_factory=dict)
    by_string: dict[str, list[str]] = field(default_factory=dict)


def build_x86_index(nodes: list[dict[str, Any]]) -> X86GraphIndex:
    """Build a multi-key index from a flat list of x86 node dicts."""
    idx = X86GraphIndex()

    def _add(d: dict[str, list[str]], key: Any, node_id: str) -> None:
        k = str(key) if key is not None else ""
        if not k:
            return
        if k not in d:
            d[k] = []
        if node_id not in d[k]:
            d[k].append(node_id)

    for node in nodes:
        nid = str(node.get("id") or "")
        if not nid:
            continue
        kind = str(node.get("kind") or "")
        addr = node.get("address")
        offset = node.get("offset")
        attrs = node.get("attributes") or {}

        if addr is not None:
            _add(idx.by_address, hex(addr), nid)
        if offset is not None:
            _add(idx.by_file_offset, hex(offset), nid)

        if kind == "section":
            _add(idx.by_section, attrs.get("name"), nid)
        elif kind == "function":
            _add(idx.by_function, attrs.get("name"), nid)
            if addr is not None:
                _add(idx.by_function, hex(addr), nid)
        elif kind == "basic_block":
            _add(idx.by_basic_block, nid, nid)
            if addr is not None:
                _add(idx.by_basic_block, hex(addr), nid)
        elif kind in {"symbol"}:
            _add(idx.by_symbol, attrs.get("name"), nid)
        elif kind == "instruction":
            _add(idx.by_mnemonic, attrs.get("mnemonic"), nid)
        elif kind == "import_symbol":
            _add(idx.by_import, attrs.get("name"), nid)
            _add(idx.by_symbol, attrs.get("name"), nid)
        elif kind == "string_literal":
            val = str(attrs.get("value") or "")
            if val:
                _add(idx.by_string, val[:64], nid)

    return idx


class X86CFGTraversal:
    """Cycle-safe bounded DFS traversal of CFG from a seed node."""

    def traverse(
        self,
        graph_index: X86GraphIndex,
        nodes_by_id: dict[str, dict[str, Any]],
        edges_by_source: dict[str, list[dict[str, Any]]],
        seed_id: str,
        max_depth: int = 20,
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        """Traverse CFG starting from seed_id.

        Returns:
          nodes: list of node dicts
          edges: list of edge dicts
          warnings: list of warning strings
        """
        visited: set[str] = set()
        result_nodes: list[dict[str, Any]] = []
        result_edges: list[dict[str, Any]] = []
        warnings: list[str] = []

        cfg_edge_types = {
            "cfg_fallthrough", "cfg_true", "cfg_false",
            "cfg_jump", "cfg_indirect_jump",
        }

        def _dfs(node_id: str, depth: int) -> None:
            if node_id in visited or depth > max_depth or len(result_nodes) >= max_nodes:
                if node_id not in visited:
                    warnings.append(f"traversal_bounded:depth={depth}:nodes={len(result_nodes)}")
                return
            visited.add(node_id)
            node = nodes_by_id.get(node_id)
            if node:
                result_nodes.append(node)
            for edge in edges_by_source.get(node_id, []):
                etype = str(edge.get("edge_type") or "")
                if etype in cfg_edge_types:
                    result_edges.append(edge)
                    target = str(edge.get("target") or "")
                    if target and target not in visited:
                        _dfs(target, depth + 1)

        _dfs(seed_id, 0)
        return {"nodes": result_nodes, "edges": result_edges, "warnings": warnings}


class X86CallGraphTraversal:
    """Traversal of call graph — direct/indirect/import/unresolved calls."""

    def traverse(
        self,
        nodes_by_id: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
        seed_id: str,
        max_depth: int = 10,
        max_nodes: int = 100,
    ) -> dict[str, Any]:
        """Traverse call graph from seed_id.

        Returns:
          nodes, edges, direct_calls, indirect_calls, import_calls, unresolved, warnings
        """
        visited: set[str] = set()
        result_nodes: list[dict[str, Any]] = []
        result_edges: list[dict[str, Any]] = []
        direct_calls: list[str] = []
        indirect_calls: list[str] = []
        import_calls: list[str] = []
        unresolved: list[str] = []
        warnings: list[str] = []

        # Build adjacency for call edges
        call_edges: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            etype = str(edge.get("edge_type") or "")
            if etype in {"calls", "indirect_calls"}:
                src = str(edge.get("source") or "")
                if src not in call_edges:
                    call_edges[src] = []
                call_edges[src].append(edge)

        def _dfs(node_id: str, depth: int) -> None:
            if node_id in visited or depth > max_depth or len(result_nodes) >= max_nodes:
                return
            visited.add(node_id)
            node = nodes_by_id.get(node_id)
            if node:
                result_nodes.append(node)
            for edge in call_edges.get(node_id, []):
                etype = str(edge.get("edge_type") or "")
                result_edges.append(edge)
                target = str(edge.get("target") or "")
                tgt_node = nodes_by_id.get(target)
                if etype == "calls":
                    if tgt_node and tgt_node.get("kind") == "import_symbol":
                        import_calls.append(target)
                    else:
                        direct_calls.append(target)
                elif etype == "indirect_calls":
                    indirect_calls.append(target)
                else:
                    unresolved.append(target)
                if target and target not in visited:
                    _dfs(target, depth + 1)

        _dfs(seed_id, 0)
        return {
            "nodes": result_nodes,
            "edges": result_edges,
            "direct_calls": direct_calls,
            "indirect_calls": indirect_calls,
            "import_calls": import_calls,
            "unresolved": unresolved,
            "warnings": warnings,
        }
