"""Contract tests for W6 (X86CC-028..032): worker-context-handoff, viewer, fixture-suite, regression, docs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path("tests/fixtures/x86")


# ===== X86CC-028: Worker context handoff =====

def test_x86_handoff_module_importable():
    """The x86 context-handoff surface must be importable as a module so workers
    can require it without crashing on import."""
    from agent.codecompass.x86 import handoff  # type: ignore[import-not-found]
    assert handoff is not None


def test_x86_handoff_builds_context_for_function(tmp_path):
    """The handoff module must expose a build_context function that turns x86
    nodes + edges into a worker-consumable context package."""
    from agent.codecompass.x86.handoff import build_x86_worker_context  # type: ignore[attr-defined]

    nodes = [
        {"id": "fn-1", "kind": "function", "source_type": "fixture",
         "address": 0x401000, "attributes": {"name": "main"}},
        {"id": "ins-1", "kind": "instruction", "source_type": "fixture",
         "address": 0x401000, "attributes": {"mnemonic": "mov", "operands": ["rax", "rdi"]}},
    ]
    edges = []
    ctx = build_x86_worker_context(nodes=nodes, edges=edges, function_id="fn-1")
    assert ctx["schema"] == "codecompass_x86_context.v1"
    assert ctx["function_id"] == "fn-1"
    assert ctx["function"]["attributes"]["name"] == "main"
    # the worker context must include the instruction belonging to this function
    assert any(i["attributes"]["mnemonic"] == "mov" for i in ctx["instructions"])


def test_x86_handoff_missing_function_id_returns_error():
    from agent.codecompass.x86.handoff import build_x86_worker_context  # type: ignore[attr-defined]
    ctx = build_x86_worker_context(nodes=[], edges=[], function_id="")
    assert ctx.get("status") == "error"


# ===== X86CC-029: Viewer model =====

def test_viewer_builds_function_overview():
    from agent.codecompass.x86.viewer import build_function_overview
    nodes = [
        {"id": "fn", "kind": "function", "source_type": "fixture",
         "address": 0x401000, "attributes": {"name": "main"}},
        {"id": "imp", "kind": "import_symbol", "source_type": "fixture",
         "address": None, "attributes": {"name": "printf", "dll": "msvcrt"}},
    ]
    edges = [{"source": "fn", "target": "imp", "edge_type": "calls"}]
    model = build_function_overview(nodes, edges)
    payload = model.as_dict()
    assert "function_overview" in payload
    assert "cfg_view" in payload
    assert "call_graph_view" in payload
    assert "section_map" in payload
    assert any(n["id"] == "fn" for n in model.function_overview.nodes)


def test_viewer_truncation_flag_when_functions_exceed_limit():
    from agent.codecompass.x86.viewer import build_function_overview
    nodes = [
        {"id": f"fn-{i}", "kind": "function", "source_type": "fixture",
         "address": 0x400000 + i, "attributes": {"name": f"f{i}"}}
        for i in range(10)
    ]
    model = build_function_overview(nodes, [], limits={"max_functions": 3})
    assert "functions_truncated" in model.function_overview.truncation_flags
    assert len(model.function_overview.nodes) <= 4  # 3 fns + maybe imports


def test_viewer_cfg_indirect_jump_warning():
    from agent.codecompass.x86.viewer import build_function_overview
    nodes = [
        {"id": "bb-1", "kind": "basic_block", "source_type": "fixture", "address": 0x401000},
    ]
    edges = [
        {"source": "bb-1", "target": "bb-unknown", "edge_type": "cfg_indirect_jump"},
    ]
    model = build_function_overview(nodes, edges)
    assert "cfg_incomplete:indirect_jump" in model.cfg_view.warnings


def test_viewer_payload_is_json_serializable():
    from agent.codecompass.x86.viewer import build_function_overview
    nodes = [
        {"id": "fn", "kind": "function", "source_type": "fixture",
         "address": 0x401000, "attributes": {"name": "main"}},
    ]
    model = build_function_overview(nodes, [])
    blob = json.dumps(model.as_dict())
    assert "function_overview" in blob


# ===== X86CC-030: Fixture suite completeness =====

EXPECTED_FIXTURE_FILES = {
    "simple_add_x86_64.json",
    "call_ret_x86_64.json",
    "if_else_x86_64.json",
    "loop_x86_64.json",
    "indirect_jump_fixture.json",
    "imports_pe_fixture.json",
    "multi_section_pe_fixture.json",
    "high_entropy_section_fixture.json",
    "anti_debug_fixture.json",
    "strings_ioc_fixture.json",
}


def test_fixture_suite_covers_required_kinds():
    """Every required fixture kind must be present (or, if missing, the test
    lists the gap so the user can decide whether to author it)."""
    actual = {p.name for p in FIXTURE_DIR.iterdir() if p.is_file()}
    missing = EXPECTED_FIXTURE_FILES - actual
    assert not missing, f"missing fixture files: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURE_FILES))
def test_each_fixture_is_valid_json_object(name):
    path = FIXTURE_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} not present")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} must be a JSON object"


@pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURE_FILES))
def test_each_fixture_has_metadata(name):
    path = FIXTURE_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} not present")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "metadata" in data
    meta = data["metadata"]
    # abi is the discriminator; pin against drift
    assert "abi" in meta


# ===== X86CC-031: Regression — existing CodeCompass tests unchanged =====

def test_existing_codecompass_graph_api_still_passes_shape():
    """Smoke check: the existing graph-store API still has the fields it had
    before the x86 extension was added."""
    from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
    import inspect
    sig = inspect.signature(CodeCompassGraphStore.rebuild_from_output_records)
    params = sig.parameters
    assert "records" in params
    assert "manifest_hash" in params


def test_existing_codecompass_load_payload_has_all_back_compat_fields():
    from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        store = CodeCompassGraphStore(index_path=os.path.join(td, "g.json"))
        payload = store.load()
    # all original fields must still be present (not removed by the x86 extension)
    for field in ("state", "nodes", "edges", "semantic_nodes", "semantic_edges",
                  "equivalence_rules", "translation_contracts", "transform_artifacts",
                  "node_index", "semantic_index", "outgoing_index", "incoming_index",
                  "diagnostics"):
        assert field in payload, f"regression: {field} missing from graph-store payload"


# ===== X86CC-032: Documentation =====

def test_documentation_present_and_complete():
    doc_path = Path("docs/codecompass-x86-assembly-core-extension.md")
    assert doc_path.is_file()
    text = doc_path.read_text(encoding="utf-8")
    # the doc must include at least one sample query so users can copy-paste
    low = text.lower()
    assert "sample" in low or "example" in low or "query" in low, (
        "doc should contain sample/example queries so users can copy-paste"
    )


def test_documentation_lists_all_five_tools():
    """The contract doc must enumerate the 5 x86 tools users can call."""
    doc_path = Path("docs/codecompass-x86-assembly-core-extension.md")
    text = doc_path.read_text(encoding="utf-8")
    for tool in ("x86_overview", "x86_address_lookup", "x86_cfg", "x86_call_graph", "x86_find"):
        assert tool in text, f"doc must list tool {tool}"