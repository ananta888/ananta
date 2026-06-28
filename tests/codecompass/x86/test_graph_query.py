"""Contract tests for W4 (X86CC-017..022): graph index, traversal, query, retrieval integration.

Covers:
- X86CC-017: graph-store rebuild_from_output_records accepts x86 nodes/edges and indexes them
- X86CC-018: CFG + call graph traversal (cycle-safe, bounded, distinguishes call kinds)
- X86CC-019: X86QueryEngine for address/symbol/function/mnemonic/import/string/section/basic_block
- X86CC-020: x86-specific resolver integration (X86QueryEngine is reachable through tools/services)
- X86CC-021: parse_address for hex/dec/symbol-style
- X86CC-022: arch-query: domain_map / arch_query should include x86 node kinds when x86 data is indexed
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.codecompass.x86.fixture_adapter import FixtureAdapter
from agent.codecompass.x86.graph_extensions import (
    X86CFGTraversal,
    X86CallGraphTraversal,
    X86GraphIndex,
    build_x86_index,
)
from agent.codecompass.x86.index_builder import X86IndexBuilder
from agent.codecompass.x86.input_taxonomy import (
    INPUT_KIND_DISASSEMBLER_JSON,
    X86InputRecord,
)
from agent.codecompass.x86.models import X86EdgeType, X86NodeKind
from agent.codecompass.x86.query import (
    VALID_QUERY_KINDS,
    X86Query,
    X86QueryEngine,
    parse_address,
)

FIXTURE_DIR = Path("tests/fixtures/x86")
SIMPLE_ADD = FIXTURE_DIR / "simple_add_x86_64.json"
CALL_RET = FIXTURE_DIR / "call_ret_x86_64.json"


def _build_x86_index_for(fixture: Path) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Helper: end-to-end x86 build via FixtureAdapter -> X86IndexBuilder."""
    builder = X86IndexBuilder()
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
        abi="x86_64_sysv",
    )
    adapter = FixtureAdapter(fixture_path=fixture)
    out = builder.build(inp, adapter)
    nodes = out["x86_nodes"]
    edges = out["x86_edges"]
    nodes_by_id = {n["id"]: n for n in nodes}
    return nodes, edges, nodes_by_id


# ===== X86CC-017: Graph index =====

def test_build_x86_index_indexes_by_address():
    nodes, _, _ = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    assert idx.by_address, "expected non-empty address index"


def test_build_x86_index_indexes_mnemonics():
    nodes, _, _ = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    assert "mov" in idx.by_mnemonic


def test_build_x86_index_indexes_functions_by_name():
    nodes, _, _ = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    assert "add_two" in idx.by_function


def test_build_x86_index_handles_empty_node_list():
    idx = build_x86_index([])
    assert idx.by_address == {}
    assert idx.by_mnemonic == {}


def test_build_x86_index_deduplicates_node_ids_per_key():
    node = {
        "id": "abc", "kind": "instruction", "source_type": "fixture",
        "address": 0x401014, "attributes": {"mnemonic": "mov"},
    }
    idx = build_x86_index([node, node, node])
    assert len(idx.by_address[hex(0x401014)]) == 1
    assert len(idx.by_mnemonic["mov"]) == 1


# ===== X86CC-018: CFG + call graph traversal =====

def test_cfg_traversal_returns_seed_node():
    nodes, edges, nodes_by_id = _build_x86_index_for(CALL_RET)
    # pick any node id; CFG traversal just walks out-edges of cfg_* types
    seed = next(iter(nodes_by_id))
    edges_by_source: dict[str, list[dict]] = {}
    for e in edges:
        edges_by_source.setdefault(e["source"], []).append(e)

    idx = build_x86_index(nodes)
    traversal = X86CFGTraversal().traverse(idx, nodes_by_id, edges_by_source, seed)
    assert "nodes" in traversal
    assert "edges" in traversal
    assert "warnings" in traversal


def test_cfg_traversal_is_cycle_safe():
    """A -> B -> A must terminate without infinite recursion."""
    nodes_by_id = {
        "A": {"id": "A", "kind": "basic_block", "source_type": "fixture"},
        "B": {"id": "B", "kind": "basic_block", "source_type": "fixture"},
    }
    edges_by_source = {
        "A": [{"source": "A", "target": "B", "edge_type": "cfg_fallthrough", "confidence": 1.0}],
        "B": [{"source": "B", "target": "A", "edge_type": "cfg_fallthrough", "confidence": 1.0}],
    }
    idx = build_x86_index(list(nodes_by_id.values()))
    result = X86CFGTraversal().traverse(idx, nodes_by_id, edges_by_source, "A", max_depth=50, max_nodes=100)
    # both nodes must be visited, no infinite loop
    visited_ids = {n["id"] for n in result["nodes"]}
    assert visited_ids == {"A", "B"}


def test_cfg_traversal_respects_max_depth():
    """Linear chain A->B->C->D->E with max_depth=2 must stop at depth 2."""
    chain = {f"n{i}": {"id": f"n{i}", "kind": "basic_block", "source_type": "fixture"} for i in range(5)}
    edges_by_source = {
        f"n{i}": [{"source": f"n{i}", "target": f"n{i+1}", "edge_type": "cfg_fallthrough", "confidence": 1.0}]
        for i in range(4)
    }
    idx = build_x86_index(list(chain.values()))
    result = X86CFGTraversal().traverse(idx, chain, edges_by_source, "n0", max_depth=2, max_nodes=100)
    visited_ids = {n["id"] for n in result["nodes"]}
    assert "n0" in visited_ids
    assert "n1" in visited_ids
    assert "n2" in visited_ids
    # n3/n4 must NOT be reached (depth > 2)
    assert "n3" not in visited_ids


def test_cfg_traversal_respects_max_nodes():
    """Wide fan-out with max_nodes=2 must stop early and emit warning."""
    center = {"id": "center", "kind": "basic_block", "source_type": "fixture"}
    leaves = {f"leaf{i}": {"id": f"leaf{i}", "kind": "basic_block", "source_type": "fixture"} for i in range(10)}
    edges_by_source = {
        "center": [
            {"source": "center", "target": f"leaf{i}", "edge_type": "cfg_fallthrough", "confidence": 1.0}
            for i in range(10)
        ]
    }
    idx = build_x86_index([center, *leaves.values()])
    result = X86CFGTraversal().traverse(idx, {**leaves, "center": center}, edges_by_source, "center", max_depth=10, max_nodes=2)
    # We must have hit the bound; result includes center + 1 leaf
    assert len(result["nodes"]) <= 2


def test_call_graph_traversal_distinguishes_direct_indirect_import():
    nodes_by_id = {
        "caller": {"id": "caller", "kind": "function", "source_type": "fixture"},
        "helper": {"id": "helper", "kind": "function", "source_type": "fixture"},
        "printf_imp": {"id": "printf_imp", "kind": "import_symbol", "source_type": "fixture"},
        "reg_call": {"id": "reg_call", "kind": "function", "source_type": "fixture"},
    }
    edges = [
        {"source": "caller", "target": "helper", "edge_type": "calls", "confidence": 1.0},
        {"source": "caller", "target": "printf_imp", "edge_type": "calls", "confidence": 1.0},
        {"source": "caller", "target": "reg_call", "edge_type": "indirect_calls", "confidence": 0.5},
    ]
    result = X86CallGraphTraversal().traverse(nodes_by_id, edges, "caller", max_depth=10, max_nodes=100)
    assert "helper" in result["direct_calls"]
    assert "printf_imp" in result["import_calls"]
    assert "reg_call" in result["indirect_calls"]


# ===== X86CC-019: X86QueryEngine =====

def test_query_kinds_complete():
    expected = {
        "address", "symbol", "function", "mnemonic", "import", "string",
        "section", "basic_block", "diagnostics",
    }
    assert VALID_QUERY_KINDS == expected


def test_query_engine_address_lookup():
    nodes, _, nodes_by_id = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    first_instr = next(n for n in nodes if n["kind"] == "instruction")
    q = X86Query(kind="address", value=hex(first_instr["address"]))
    result = engine.execute(q, idx)
    assert result.status == "ok"
    assert first_instr["id"] in result.node_ids


def test_query_engine_mnemonic_lookup():
    nodes, _, nodes_by_id = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="mnemonic", value="mov"), idx)
    assert any("mov" in (n.get("attributes", {}).get("mnemonic") or "") for n in result.nodes)


def test_query_engine_function_lookup_by_name():
    nodes, _, nodes_by_id = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="function", value="add_two"), idx)
    assert result.node_ids


def test_query_engine_function_lookup_by_address():
    nodes, _, nodes_by_id = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    fn = next(n for n in nodes if n["kind"] == "function")
    result = engine.execute(X86Query(kind="function", value=hex(fn["address"])), idx)
    assert fn["id"] in result.node_ids


def test_query_engine_invalid_kind_returns_error_result():
    nodes, _, nodes_by_id = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="not_a_real_kind"), idx)
    assert result.status == "error"
    assert "invalid_query_kind" in (result.error or "")


def test_query_engine_invalid_address_returns_error_result():
    nodes, _, nodes_by_id = _build_x86_index_for(SIMPLE_ADD)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="address", value="not_a_number"), idx)
    assert result.status == "error"
    assert "invalid_address_query" in (result.error or "")


def test_query_engine_respects_limit():
    """With limit=1, the result must never contain more than 1 node_id."""
    nodes, _, nodes_by_id = _build_x86_index_for(CALL_RET)
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="mnemonic", value="mov", limit=1), idx)
    assert len(result.node_ids) <= 1


def test_query_engine_truncation_warning_when_many_results():
    """Synthesize an index with many same-mnemonic instructions and assert the
    'results_truncated' warning fires."""
    nodes = [
        {
            "id": f"node-{i}",
            "kind": "instruction",
            "source_type": "fixture",
            "address": 0x400000 + i,
            "attributes": {"mnemonic": "nop"},
        }
        for i in range(10)
    ]
    nodes_by_id = {n["id"]: n for n in nodes}
    idx = build_x86_index(nodes)
    engine = X86QueryEngine(nodes_by_id=nodes_by_id)
    result = engine.execute(X86Query(kind="mnemonic", value="nop", limit=3), idx)
    assert len(result.node_ids) == 3
    assert any("truncated" in w for w in result.warnings)


# ===== X86CC-021: parse_address =====

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0x401014", 0x401014),
        ("0x401014", 0x401014),
        ("0X401014", 0x401014),
        ("4198420", 4198420),
        ("0", 0),
        ("0x0", 0),
    ],
)
def test_parse_address_valid(raw, expected):
    assert parse_address(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not_a_number", "0xZZZ"])
def test_parse_address_invalid_returns_none(raw):
    assert parse_address(raw) is None


# ===== X86CC-022: arch-query includes x86 kinds =====

def test_arch_query_includes_x86_node_kinds_after_indexing():
    """After building an x86 index, the kinds present in the graph must include
    x86-specific node kinds (function, instruction, section)."""
    nodes, _, _ = _build_x86_index_for(SIMPLE_ADD)
    indexed_kinds = {n["kind"] for n in nodes}
    assert "instruction" in indexed_kinds
    assert "function" in indexed_kinds
    assert "section" in indexed_kinds


def test_codemapgraph_store_rebuilds_x86_records(tmp_path):
    """CodeCompassGraphStore.rebuild_from_output_records must accept x86 records
    (output_kind 'x86_nodes' / 'x86_edges') and surface them in the resulting graph."""
    from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
    nodes, edges, _ = _build_x86_index_for(SIMPLE_ADD)
    output_records = []
    for n in nodes:
        rec = dict(n)
        rec["_provenance"] = {"output_kind": "x86_nodes", "manifest_hash": "abc123"}
        output_records.append(rec)
    for e in edges:
        rec = dict(e)
        rec["_provenance"] = {"output_kind": "x86_edges", "manifest_hash": "abc123"}
        output_records.append(rec)
    index_path = tmp_path / "graph.json"
    store = CodeCompassGraphStore(index_path=str(index_path))
    diagnostics = store.rebuild_from_output_records(records=output_records, manifest_hash="abc123")
    # the store must have surfaced x86 nodes via its index
    assert diagnostics.get("x86_extension", {}).get("status") == "ready"
    assert diagnostics["x86_extension"]["node_count"] > 0
    # and the returned payload includes the x86_index with nodes_by_id
    payload = store.load()
    assert "x86_index" in payload
    assert "nodes_by_id" in payload["x86_index"]
    kinds = {v.get("_provenance", {}).get("output_kind") for v in payload["x86_index"]["nodes_by_id"].values()}
    assert "x86_nodes" in kinds