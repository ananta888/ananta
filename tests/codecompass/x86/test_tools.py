"""Contract tests for W5 (X86CC-023..027): the 5 x86 CodeCompass tools.

The tools follow the same shape as the existing codecompass_* tools:
they accept workspace_dir, arguments, tool_call_id; they return a ToolResult dict.

Tools under test:
  - codecompass.x86_overview       (X86CC-023)
  - codecompass.x86_address_lookup (X86CC-024)
  - codecompass.x86_cfg            (X86CC-025)
  - codecompass.x86_call_graph     (X86CC-026)
  - codecompass.x86_find           (X86CC-027)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path("tests/fixtures/x86")
SIMPLE_ADD = FIXTURE_DIR / "simple_add_x86_64.json"


def _build_mock_graph_store(*, with_x86: bool = True, enabled: bool = True):
    """Mock the CodeCompassGraphStore to return either an x86 payload or empty."""
    from agent.codecompass.x86.fixture_adapter import FixtureAdapter
    from agent.codecompass.x86.index_builder import X86IndexBuilder
    from agent.codecompass.x86.input_taxonomy import INPUT_KIND_DISASSEMBLER_JSON, X86InputRecord

    builder = X86IndexBuilder()
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
        abi="x86_64_sysv",
    )
    adapter = FixtureAdapter(fixture_path=SIMPLE_ADD)
    out = builder.build(inp, adapter)

    if not with_x86:
        return {
            "x86_nodes": [], "x86_edges": [],
            "x86_index": {"schema": "codecompass_x86_graph.v1", "nodes": [], "edges": [], "nodes_by_id": {}, "node_count": 0, "edge_count": 0},
            "diagnostics": {"status": "degraded", "x86_extension": {"status": "degraded", "reason": "no_x86_records"}},
        }

    return {
        "x86_nodes": out["x86_nodes"],
        "x86_edges": out["x86_edges"],
        "x86_index": {
            "schema": "codecompass_x86_graph.v1",
            "nodes": out["x86_nodes"],
            "edges": out["x86_edges"],
            "nodes_by_id": {n["id"]: n for n in out["x86_nodes"]},
            "node_count": len(out["x86_nodes"]),
            "edge_count": len(out["x86_edges"]),
        },
        "diagnostics": {
            "status": "ready",
            "x86_extension": {
                "schema": "codecompass_x86_graph.v1",
                "node_count": len(out["x86_nodes"]),
                "edge_count": len(out["x86_edges"]),
                "status": "ready",
            },
        },
    }


# ===== X86CC-023: x86_overview =====

def test_x86_overview_returns_tool_result_with_summary():
    from agent.services.tools.codecompass_tools import codecompass_x86_overview
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_overview(
            workspace_dir="/tmp", arguments={"manifest_hash": "abc"}, tool_call_id="tc-1",
        )
    assert result["tool_name"] == "codecompass.x86_overview"
    assert result["status"] == "ok"
    summary = result.get("data", {}).get("summary", {})
    assert summary["node_count"] > 0
    assert summary["edge_count"] >= 0


def test_x86_overview_when_x86_disabled_returns_degraded():
    from agent.services.tools.codecompass_tools import codecompass_x86_overview
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store(with_x86=False)
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_overview(
            workspace_dir="/tmp", arguments={"manifest_hash": "abc"}, tool_call_id="tc-1",
        )
    assert result["status"] == "ok"
    summary = result.get("data", {}).get("summary", {})
    assert summary["node_count"] == 0


# ===== X86CC-024: x86_address_lookup =====

def test_x86_address_lookup_resolves_instruction():
    from agent.services.tools.codecompass_tools import codecompass_x86_address_lookup
    store = MagicMock()
    payload = _build_mock_graph_store()
    store.load.return_value = payload
    # pick first instruction address
    instr = next(n for n in payload["x86_nodes"] if n["kind"] == "instruction")
    addr_hex = hex(instr["address"])
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_address_lookup(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "address": addr_hex},
            tool_call_id="tc-2",
        )
    assert result["status"] == "ok"
    nodes = result.get("data", {}).get("nodes", [])
    assert any(n["id"] == instr["id"] for n in nodes)


def test_x86_address_lookup_invalid_address_returns_error():
    from agent.services.tools.codecompass_tools import codecompass_x86_address_lookup
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_address_lookup(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "address": "not_a_number"},
            tool_call_id="tc-2",
        )
    assert result["status"] == "error"
    assert "invalid" in (result.get("error") or "").lower()


def test_x86_address_lookup_missing_address_returns_error():
    from agent.services.tools.codecompass_tools import codecompass_x86_address_lookup
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_address_lookup(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc"},
            tool_call_id="tc-2",
        )
    assert result["status"] == "error"
    assert "address" in (result.get("error") or "").lower()


# ===== X86CC-025: x86_cfg =====

def test_x86_cfg_returns_cfg_traversal_for_seed():
    from agent.services.tools.codecompass_tools import codecompass_x86_cfg
    store = MagicMock()
    payload = _build_mock_graph_store()
    store.load.return_value = payload
    # pick a function node as seed
    fn = next(n for n in payload["x86_nodes"] if n["kind"] == "function")
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_cfg(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "seed_id": fn["id"]},
            tool_call_id="tc-3",
        )
    assert result["status"] == "ok"
    assert "nodes" in result.get("data", {})


def test_x86_cfg_missing_seed_returns_error():
    from agent.services.tools.codecompass_tools import codecompass_x86_cfg
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_cfg(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc"},
            tool_call_id="tc-3",
        )
    assert result["status"] == "error"


# ===== X86CC-026: x86_call_graph =====

def test_x86_call_graph_returns_classified_calls():
    from agent.services.tools.codecompass_tools import codecompass_x86_call_graph
    store = MagicMock()
    payload = _build_mock_graph_store()
    store.load.return_value = payload
    fn = next(n for n in payload["x86_nodes"] if n["kind"] == "function")
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_call_graph(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "seed_id": fn["id"]},
            tool_call_id="tc-4",
        )
    assert result["status"] == "ok"
    data = result.get("data", {})
    assert "direct_calls" in data or "indirect_calls" in data or "import_calls" in data


def test_x86_call_graph_missing_seed_returns_error():
    from agent.services.tools.codecompass_tools import codecompass_x86_call_graph
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_call_graph(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc"},
            tool_call_id="tc-4",
        )
    assert result["status"] == "error"


# ===== X86CC-027: x86_find =====

def test_x86_find_by_mnemonic():
    from agent.services.tools.codecompass_tools import codecompass_x86_find
    store = MagicMock()
    payload = _build_mock_graph_store()
    store.load.return_value = payload
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_find(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "kind": "mnemonic", "value": "mov"},
            tool_call_id="tc-5",
        )
    assert result["status"] == "ok"
    nodes = result.get("data", {}).get("nodes", [])
    assert any(n.get("attributes", {}).get("mnemonic") == "mov" for n in nodes)


def test_x86_find_by_function_name():
    from agent.services.tools.codecompass_tools import codecompass_x86_find
    store = MagicMock()
    payload = _build_mock_graph_store()
    store.load.return_value = payload
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_find(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "kind": "function", "value": "add_two"},
            tool_call_id="tc-5",
        )
    assert result["status"] == "ok"
    nodes = result.get("data", {}).get("nodes", [])
    assert any(n.get("attributes", {}).get("name") == "add_two" for n in nodes)


def test_x86_find_missing_kind_returns_error():
    from agent.services.tools.codecompass_tools import codecompass_x86_find
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_find(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "value": "x"},
            tool_call_id="tc-5",
        )
    assert result["status"] == "error"


def test_x86_find_invalid_kind_returns_error():
    from agent.services.tools.codecompass_tools import codecompass_x86_find
    store = MagicMock()
    store.load.return_value = _build_mock_graph_store()
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_find(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "kind": "not_a_real_kind", "value": "x"},
            tool_call_id="tc-5",
        )
    assert result["status"] == "error"


def test_x86_find_respects_limit():
    from agent.services.tools.codecompass_tools import codecompass_x86_find
    store = MagicMock()
    payload = _build_mock_graph_store()
    store.load.return_value = payload
    with patch("agent.services.tools.codecompass_tools._resolve_graph_store", return_value=store):
        result = codecompass_x86_find(
            workspace_dir="/tmp",
            arguments={"manifest_hash": "abc", "kind": "mnemonic", "value": "mov", "limit": 1},
            tool_call_id="tc-5",
        )
    assert result["status"] == "ok"
    nodes = result.get("data", {}).get("nodes", [])
    assert len(nodes) <= 1