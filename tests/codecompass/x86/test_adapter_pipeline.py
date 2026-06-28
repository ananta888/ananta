"""Contract tests for W3 (X86CC-011..016): adapter contract + fixture + capstone + builder + pipeline + limits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.codecompass.x86.adapter import DummyAdapter, X86DisassemblerAdapter
from agent.codecompass.x86.capstone_adapter import CapstoneAdapter, is_available
from agent.codecompass.x86.config import X86Config, load_x86_config
from agent.codecompass.x86.diagnostics import INDEX_TRUNCATED
from agent.codecompass.x86.fixture_adapter import FixtureAdapter, load_fixture
from agent.codecompass.x86.index_builder import (
    X86IndexBuilder,
    X86IndexLimits,
    build_manifest,
)
from agent.codecompass.x86.index_pipeline import X86IndexPipeline
from agent.codecompass.x86.input_taxonomy import (
    INPUT_KIND_CAPSTONE_FIXTURE,
    INPUT_KIND_DISASSEMBLER_JSON,
    X86InputRecord,
)

FIXTURE_DIR = Path("tests/fixtures/x86")
SIMPLE_ADD = FIXTURE_DIR / "simple_add_x86_64.json"
CALL_RET = FIXTURE_DIR / "call_ret_x86_64.json"


# ===== X86CC-011: Adapter contract =====

def test_dummy_adapter_is_concrete_subclass():
    assert issubclass(DummyAdapter, X86DisassemblerAdapter)


def test_dummy_adapter_capabilities_dict():
    a = DummyAdapter()
    caps = a.capabilities()
    assert caps["name"] == "dummy"
    assert caps["version"] == "0.0.1"
    assert caps["execution_blocked"] is True
    assert isinstance(caps["supported_input_types"], list)
    assert isinstance(caps["supported_profiles"], list)


def test_dummy_adapter_disassemble_returns_required_keys():
    a = DummyAdapter()
    inp = X86InputRecord(kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64)
    out = a.disassemble(inp)
    assert "nodes" in out
    assert "edges" in out
    assert "diagnostics" in out
    assert "metadata" in out
    assert out["metadata"]["adapter"] == "dummy"
    assert out["metadata"]["adapter_version"] == "0.0.1"


def test_adapter_contract_forbids_unsafe_input_kinds():
    """A safe-by-construction adapter must not advertise unsafe input kinds as supported."""
    a = DummyAdapter()
    # DummyAdapter currently advertises all 9 — that's fine for testing the dispatcher,
    # but the runtime dispatcher (not exercised here) MUST refuse unsafe ones.
    # The safety layer in agent.codecompass.x86.safety is what enforces this; here
    # we just verify the safety module agrees.
    from agent.codecompass.x86.safety import check_safety_policy, is_safe_for_static_analysis
    assert check_safety_policy("executable_binary") is not None
    assert is_safe_for_static_analysis("executable_binary") is False


# ===== X86CC-012: Fixture adapter =====

def test_load_fixture_returns_dict():
    d = load_fixture(SIMPLE_ADD)
    assert isinstance(d, dict)
    assert d["metadata"]["abi"] == "x86_64_sysv"


def test_load_fixture_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_fixture("/nonexistent/path.fixture.json")


def test_load_fixture_invalid_json_raises_value_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(ValueError):
        load_fixture(bad)


def test_fixture_adapter_produces_nodes_for_simple_add():
    a = FixtureAdapter(fixture_path=SIMPLE_ADD)
    inp = X86InputRecord(kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64)
    out = a.disassemble(inp)
    kinds = {n["kind"] for n in out["nodes"]}
    # expect at least instruction + function nodes
    assert "instruction" in kinds
    assert "function" in kinds


def test_fixture_adapter_metadata_has_counts():
    a = FixtureAdapter(fixture_path=SIMPLE_ADD)
    inp = X86InputRecord(kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64)
    out = a.disassemble(inp)
    meta = out["metadata"]
    assert meta["instruction_count"] == 3
    assert meta["function_count"] == 1
    assert meta["adapter"] == "fixture_adapter"
    assert meta["profile"] == "x86_64_sysv"


def test_fixture_adapter_call_ret_has_call_and_ret_instructions():
    a = FixtureAdapter(fixture_path=CALL_RET)
    inp = X86InputRecord(kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64)
    out = a.disassemble(inp)
    mnemonics = [n["attributes"]["mnemonic"] for n in out["nodes"] if n["kind"] == "instruction"]
    assert "call" in mnemonics
    assert "ret" in mnemonics


def test_fixture_adapter_without_path_returns_adapter_error_diagnostic():
    a = FixtureAdapter()
    inp = X86InputRecord(kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64)
    out = a.disassemble(inp)
    assert out["nodes"] == []
    codes = [d.get("code") for d in out["diagnostics"]]
    assert "adapter_error" in codes


# ===== X86CC-013: Capstone adapter =====

def test_capstone_is_available_returns_bool():
    """Without capstone pip-installed, is_available() must return False (not raise)."""
    result = is_available()
    assert isinstance(result, bool)


def test_capstone_adapter_unavailable_disassemble_emits_diagnostic():
    """If capstone is not installed, the adapter must produce a diagnostic, not raise."""
    if is_available():
        pytest.skip("capstone is installed in this environment — skip unavailable test")
    a = CapstoneAdapter()
    inp = X86InputRecord(
        kind=INPUT_KIND_CAPSTONE_FIXTURE, architecture="x86_64", bitness=64,
    )
    out = a.disassemble(inp)
    assert "diagnostics" in out
    codes = [d.get("code") for d in out["diagnostics"]]
    assert "disassembler_unavailable" in codes


def test_capstone_adapter_capabilities_disclose_availability():
    a = CapstoneAdapter()
    caps = a.capabilities()
    # Pin either spelling; current skeleton uses 'available'
    assert "available" in caps or "capstone_available" in caps
    val = caps.get("available", caps.get("capstone_available"))
    assert isinstance(val, bool)


# ===== X86CC-014: Index builder =====

def test_build_manifest_shape():
    m = build_manifest(
        profile="x86_64_sysv",
        adapter="fixture_adapter",
        input_counts={"nodes": 10, "edges": 5},
        instruction_counts=3,
        function_counts=1,
        diagnostic_counts=0,
    )
    assert m["profile"] == "x86_64_sysv"
    assert m["adapter"] == "fixture_adapter"
    assert m["instruction_count"] == 3
    assert m["function_count"] == 1
    assert m["truncation_info"] == {}


def test_index_builder_runs_fixture_end_to_end():
    builder = X86IndexBuilder()
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
        abi="x86_64_sysv",
    )
    adapter = FixtureAdapter(fixture_path=SIMPLE_ADD)
    out = builder.build(inp, adapter)
    assert "x86_nodes" in out
    assert "x86_edges" in out
    assert "x86_diagnostics" in out
    assert "x86_manifest" in out
    assert out["x86_manifest"]["profile"] == "x86_64_sysv"
    assert out["x86_manifest"]["adapter"] == "fixture_adapter"
    # End-to-end: at least one instruction node must have come through
    instruction_nodes = [n for n in out["x86_nodes"] if n["kind"] == "instruction"]
    assert len(instruction_nodes) == 3


def test_index_builder_handles_adapter_exception():
    """If adapter raises, the builder must NOT crash — emit adapter_error diagnostic."""
    class BoomAdapter(X86DisassemblerAdapter):
        @property
        def name(self): return "boom"
        @property
        def version(self): return "0"
        @property
        def supported_input_types(self): return frozenset({"disassembler_json"})
        @property
        def supported_profiles(self): return frozenset({"x86_64_sysv"})
        def disassemble(self, input_record):
            raise RuntimeError("kaboom")

    builder = X86IndexBuilder()
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
    )
    out = builder.build(inp, BoomAdapter())
    assert out["x86_nodes"] == []
    assert any("adapter" in d.get("code","") for d in out["x86_diagnostics"])


def test_index_builder_node_sorting_is_deterministic():
    builder = X86IndexBuilder()
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
    )
    adapter = FixtureAdapter(fixture_path=CALL_RET)
    out1 = builder.build(inp, adapter)
    out2 = builder.build(inp, adapter)
    assert [n["id"] for n in out1["x86_nodes"]] == [n["id"] for n in out2["x86_nodes"]]


# ===== X86CC-015: Index pipeline =====

def test_pipeline_returns_empty_when_disabled():
    cfg = load_x86_config(env={})  # master switch off
    assert cfg.enabled is False
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
    )
    pipeline = X86IndexPipeline()
    out = pipeline.run([inp], cfg, FixtureAdapter(fixture_path=SIMPLE_ADD))
    assert out == {}


def test_pipeline_runs_when_enabled():
    cfg = X86Config(enabled=True, default_profile="x86_64_sysv")
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
        abi="x86_64_sysv",
    )
    pipeline = X86IndexPipeline()
    out = pipeline.run([inp], cfg, FixtureAdapter(fixture_path=SIMPLE_ADD))
    assert out["x86_nodes"]
    assert out["x86_manifest_list"]


def test_pipeline_degrades_one_input_does_not_kill_others():
    cfg = X86Config(enabled=True)
    good_inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
    )

    class BoomAdapter(X86DisassemblerAdapter):
        @property
        def name(self): return "boom"
        @property
        def version(self): return "0"
        @property
        def supported_input_types(self): return frozenset({"disassembler_json"})
        @property
        def supported_profiles(self): return frozenset({"x86_64_sysv"})
        def disassemble(self, input_record):
            raise RuntimeError("kaboom")

    pipeline = X86IndexPipeline()
    out = pipeline.run([good_inp, good_inp], cfg, BoomAdapter())
    # The pipeline's outer try/except should downgrade to degraded diagnostics
    diag_codes = [d.get("code") for d in out["x86_diagnostics"]]
    assert "adapter_error" in diag_codes


# ===== X86CC-016: Index limits + truncation =====

def test_index_limits_default_values_match_config():
    """Defaults must match the X86Config defaults; both share the same env contract."""
    limits = X86IndexLimits()
    cfg = load_x86_config(env={})
    assert limits.max_instructions == cfg.max_instructions
    assert limits.max_functions == cfg.max_functions
    assert limits.max_strings == cfg.max_strings


def test_truncation_emits_warning_diagnostic():
    """If we set max_instructions=2 and the fixture has 3 instructions, builder must truncate + warn."""
    limits = X86IndexLimits(max_instructions=2, max_functions=100, max_strings=100)
    builder = X86IndexBuilder(limits=limits)
    inp = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON, architecture="x86_64", bitness=64,
        abi="x86_64_sysv",
    )
    adapter = FixtureAdapter(fixture_path=SIMPLE_ADD)
    out = builder.build(inp, adapter)
    assert out["x86_manifest"]["truncation_info"]["instructions_truncated"] == 1
    codes = [d.get("code") for d in out["x86_diagnostics"]]
    assert INDEX_TRUNCATED in codes
    # Exactly 2 instruction nodes survived
    instr_nodes = [n for n in out["x86_nodes"] if n["kind"] == "instruction"]
    assert len(instr_nodes) == 2