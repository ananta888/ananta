"""VPTEST-001 + VPTEST-003: Backend tests for VP Runtime Truth.

Verifies:
  - All task kinds have required runtime-truth fields (VPRT-001/002)
  - No misleading labels (no Transformer-Encoder, Leiden/Louvain, LLM Query Rewrite)
  - Canonical names, not legacy names, are primary
  - Policy-Hints and Runtime-Truth are consistent
  - Execution plan distinguishes registered vs. executable (VPTEST-003)
  - Validator reacts correctly to registered_only / requires_approval (VPTEST-001)
"""
from __future__ import annotations

import pytest

from agent.visual_process.task_kind_registry import (
    LEGACY_MAP,
    get_task_kind_info,
    list_task_kinds,
)
from agent.visual_process.policy_hints import _KIND_HINTS as POLICY_HINTS, HINT_LLM_CALL
from agent.visual_process.step_executor import get_step_executor
from agent.visual_process.models import VisualProcessGraph, VisualProcessStep, StepIOContract
from agent.visual_process.validator import GraphValidator


_REQUIRED_RT_FIELDS = [
    "implementation_status",
    "implementation_state",
    "backend_service",
    "deterministic",
    "uses_llm",
    "uses_network",
    "side_effects",
    "risk_level",
    "legacy_aliases",
    "requires_approval",
]

_ALLOWED_STATUSES = {"production", "experimental", "stub", "test_only", "design_only", "unknown"}
_ALLOWED_STATES = {
    "wired_and_executable", "registered_only", "implemented_not_exposed",
    "exposed_not_wired", "legacy_alias", "not_implemented",
}
_ALLOWED_RISKS = {"none", "low", "medium", "high", "critical"}


# ── VPTEST-001: Runtime-Truth field completeness ──────────────────────────────

def test_all_kinds_have_required_rt_fields():
    """Every registered kind must have all 10 runtime-truth fields."""
    kinds = list_task_kinds()
    assert len(kinds) >= 35, "Expected at least 35 registered kinds"
    for k in kinds:
        missing = [f for f in _REQUIRED_RT_FIELDS if f not in k]
        assert not missing, f"Kind '{k['id']}' is missing RT fields: {missing}"


def test_implementation_status_valid_values():
    for k in list_task_kinds():
        assert k["implementation_status"] in _ALLOWED_STATUSES, (
            f"Kind '{k['id']}' has invalid implementation_status={k['implementation_status']!r}"
        )


def test_implementation_state_valid_values():
    for k in list_task_kinds():
        assert k["implementation_state"] in _ALLOWED_STATES, (
            f"Kind '{k['id']}' has invalid implementation_state={k['implementation_state']!r}"
        )


def test_risk_level_valid_values():
    for k in list_task_kinds():
        assert k["risk_level"] in _ALLOWED_RISKS, (
            f"Kind '{k['id']}' has invalid risk_level={k['risk_level']!r}"
        )


# ── VPTEST-001: Canonical kind correctness ────────────────────────────────────

def test_embed_api_is_canonical_not_vector_encode():
    """embed_api must be the canonical kind; vector_encode is only a legacy alias."""
    info = get_task_kind_info("embed_api")
    assert info is not None
    assert info["id"] == "embed_api"
    assert "vector_encode" in info["legacy_aliases"]
    assert get_task_kind_info("vector_encode") is None, (
        "vector_encode must not be a canonical kind — it is only in LEGACY_MAP"
    )


def test_turboquant_mse_is_experimental_not_stub_not_production():
    """TQ-012 TurboQuantMseEncoder: experimental (works), not stub, not full production."""
    info = get_task_kind_info("turboquant_mse")
    assert info is not None
    assert info["implementation_status"] == "experimental", (
        "turboquant_mse must be experimental, not stub and not production"
    )
    assert info["implementation_state"] == "wired_and_executable", (
        "turboquant_mse encode/decode works — should be wired_and_executable"
    )
    assert "turboquant_encode" in info["legacy_aliases"]


def test_sign_rotation_is_production_deterministic():
    """TQ-011 DeterministicSignRotation: production, deterministic, no LLM, no network."""
    info = get_task_kind_info("sign_rotation")
    assert info is not None
    assert info["implementation_status"] == "production"
    assert info["deterministic"] is True
    assert info["uses_llm"] is False
    assert info["uses_network"] is False


def test_query_rewrite_no_llm_no_network():
    """query_rewrite is rule-based synonym expansion — no LLM, no network."""
    info = get_task_kind_info("query_rewrite")
    assert info is not None
    assert info["uses_llm"] is False, "query_rewrite must NOT use LLM"
    assert info["uses_network"] is False
    assert info["deterministic"] is True


def test_domain_cluster_no_leiden_louvain_claim():
    """domain_cluster description must not positively claim Leiden/Louvain/KMeans are in production.
    It may mention them as NOT implemented (e.g. 'existieren NICHT') — that is correct."""
    info = get_task_kind_info("domain_cluster")
    assert info is not None
    desc = (info.get("description") or "").lower()
    # The description must include a negation when mentioning Leiden/Louvain/KMeans
    if "leiden" in desc:
        assert "nicht" in desc or "not" in desc or "no " in desc or "kein" in desc, (
            "If domain_cluster description mentions Leiden, it must negate it (e.g. 'existieren NICHT')"
        )
    if "louvain" in desc:
        assert "nicht" in desc or "not" in desc or "no " in desc or "kein" in desc, (
            "If domain_cluster description mentions Louvain, it must negate it"
        )
    # Ensure description marks this as deterministic/signal-based, not ML-cluster
    assert "deterministisch" in desc or "deterministic" in desc or "signal" in desc, (
        "domain_cluster description should describe deterministic signal-based clustering"
    )


def test_embed_api_no_local_pytorch_claim():
    """embed_api must not claim local PyTorch/Transformer execution."""
    info = get_task_kind_info("embed_api")
    assert info is not None
    desc = (info.get("description") or "").upper()
    assert "KEIN" in desc or "NO" in desc or "NOT" in desc, (
        "embed_api description should clarify NO local PyTorch/Transformer"
    )


def test_evolution_apply_requires_approval():
    """evolution_apply always requires gate/approval."""
    info = get_task_kind_info("evolution_apply")
    assert info is not None
    assert info["requires_approval"] is True
    assert info["risk_level"] == "high"


def test_evolve_project_requires_approval_and_critical_risk():
    info = get_task_kind_info("evolve_project")
    assert info is not None
    assert info["requires_approval"] is True
    assert info["risk_level"] == "critical"


def test_legacy_map_uses_canonical_targets():
    """All LEGACY_MAP values must resolve to valid canonical kinds."""
    for legacy, canonical in LEGACY_MAP.items():
        assert get_task_kind_info(canonical) is not None, (
            f"LEGACY_MAP['{legacy}'] -> '{canonical}' but '{canonical}' is not in _KIND_INFO"
        )
        assert get_task_kind_info(legacy) is None, (
            f"Legacy kind '{legacy}' should NOT be in _KIND_INFO directly"
        )


# ── VPTEST-001: Policy-Hints consistency ─────────────────────────────────────

def test_query_rewrite_no_llm_hint():
    """query_rewrite must not have HINT_LLM_CALL in its policy hints."""
    hints = POLICY_HINTS.get("query_rewrite", [])
    assert HINT_LLM_CALL not in hints, (
        f"query_rewrite must not have llm_call hint; got: {hints}"
    )


def test_shell_execute_has_high_risk_hint():
    hints = POLICY_HINTS.get("shell_execute", [])
    assert any("risk" in h or "shell" in h or "write" in h for h in hints), (
        f"shell_execute should have some risk/write hint; got: {hints}"
    )


# ── VPTEST-001: Validator checks ──────────────────────────────────────────────

def _make_graph(*steps: VisualProcessStep) -> VisualProcessGraph:
    return VisualProcessGraph(
        id="test-graph", name="Test Graph", version="1.0",
        steps=list(steps), edges=[],
    )


def _step(kind: str, gate: bool = False, **kwargs) -> VisualProcessStep:
    return VisualProcessStep(
        id=f"s-{kind}", label=f"Step {kind}", kind=kind,
        io=StepIOContract(), position={"x": 0, "y": 0},
        gate=gate, **kwargs,
    )


def test_validator_warns_legacy_vector_encode():
    """Graph with kind=vector_encode gets legacy_task_kind warning with replacement=embed_api."""
    validator = GraphValidator()
    graph = _make_graph(_step("vector_encode"))
    result = validator.validate(graph)
    codes = [i.code for i in result.issues]
    assert "legacy_task_kind" in codes
    msg = next(i.message for i in result.issues if i.code == "legacy_task_kind")
    assert "embed_api" in msg


def test_validator_errors_evolution_apply_without_gate():
    """evolution_apply without gate must be a hard error."""
    validator = GraphValidator()
    graph = _make_graph(_step("evolution_apply", gate=False))
    result = validator.validate(graph)
    errors = [i.code for i in result.issues if i.severity == "error"]
    assert "evolution_apply_requires_gate" in errors


def test_validator_evolution_apply_with_gate_no_error():
    validator = GraphValidator()
    graph = _make_graph(_step("evolution_apply", gate=True))
    result = validator.validate(graph)
    errors = [i.code for i in result.issues if i.severity == "error"]
    assert "evolution_apply_requires_gate" not in errors


def test_validator_warns_registered_only_steps():
    """registered_only steps get step_not_executable warning."""
    validator = GraphValidator()
    graph = _make_graph(_step("codecompass_vector_search"))
    result = validator.validate(graph)
    codes = [i.code for i in result.issues]
    assert "step_not_executable" in codes


def test_validator_no_warning_for_wired_executable():
    """wired_and_executable steps must not get step_not_executable warning."""
    validator = GraphValidator()
    graph = _make_graph(_step("query_rewrite"))
    result = validator.validate(graph)
    codes = [i.code for i in result.issues]
    assert "step_not_executable" not in codes


def test_validator_requires_approval_no_gate_warning():
    """evolve_project without gate gets requires_approval_no_gate warning."""
    validator = GraphValidator()
    graph = _make_graph(_step("evolve_project", gate=False))
    result = validator.validate(graph)
    codes = [i.code for i in result.issues]
    assert "requires_approval_no_gate" in codes


# ── VPTEST-003: Execution plan registered vs. executable ─────────────────────

def test_execution_plan_worker_dispatch():
    executor = get_step_executor()
    steps = [_step("patch_apply")]
    plan = executor.execution_plan(steps)
    assert plan[0].execution_mode == "worker_dispatch"
    assert plan[0].executable is True


def test_execution_plan_vp_adapter():
    executor = get_step_executor()
    steps = [_step("query_rewrite")]
    plan = executor.execution_plan(steps)
    assert plan[0].execution_mode == "vp_adapter"
    assert plan[0].executable is True


def test_execution_plan_not_executable():
    executor = get_step_executor()
    steps = [_step("codecompass_vector_search")]
    plan = executor.execution_plan(steps)
    assert plan[0].execution_mode == "not_executable"
    assert plan[0].executable is False
    assert "registered_only" in plan[0].execution_reason


def test_execution_plan_mixed():
    """Graph with both executable and registered_only steps."""
    executor = get_step_executor()
    steps = [
        _step("sign_rotation"),            # vp_adapter
        _step("shell_execute"),             # worker_dispatch
        _step("evolution_analyze"),         # not_executable
        _step("codecompass_fts_search"),    # not_executable
    ]
    plan = executor.execution_plan(steps)
    modes = {p.kind: p.execution_mode for p in plan}
    assert modes["sign_rotation"] == "vp_adapter"
    assert modes["shell_execute"] == "worker_dispatch"
    assert modes["evolution_analyze"] == "not_executable"
    assert modes["codecompass_fts_search"] == "not_executable"
    non_exec = [p for p in plan if not p.executable]
    assert len(non_exec) == 2


def test_execution_plan_rt_fields_present():
    """Execution plan entries expose runtime-truth fields."""
    executor = get_step_executor()
    plan = executor.execution_plan([_step("turboquant_mse")])
    p = plan[0]
    assert p.implementation_status == "experimental"
    assert p.uses_llm is False
    assert p.uses_network is False
    assert p.deterministic is True
    assert p.risk_level == "none"


def test_vp_adapter_query_rewrite_executes():
    """VP adapter for query_rewrite returns synonym-expanded results."""
    executor = get_step_executor()
    step = _step("query_rewrite", metadata={"query": "fix bug"})
    result = executor.execute(step, artifacts={"query": "fix bug"}, context={})
    assert result.status == "success"
    assert result.executable is True
    assert "rewritten" in result.outputs
    # "fix" expands to repair/resolve
    assert result.outputs["rewritten"] != result.outputs.get("original", "")


def test_vp_adapter_sign_rotation_executes():
    """VP adapter for sign_rotation runs DeterministicSignRotation."""
    executor = get_step_executor()
    vector = [0.1, -0.5, 0.3, 0.8, -0.2]
    step = _step("sign_rotation")
    result = executor.execute(step, artifacts={"vector": vector}, context={})
    assert result.status == "success"
    rotated = result.outputs["rotated"]
    assert len(rotated) == len(vector)
    # DeterministicSignRotation is self-inverse
    step2 = _step("sign_rotation")
    result2 = executor.execute(step2, artifacts={"vector": rotated}, context={})
    double_rotated = result2.outputs["rotated"]
    assert all(abs(a - b) < 1e-9 for a, b in zip(vector, double_rotated)), (
        "DeterministicSignRotation must be self-inverse"
    )


def test_not_wired_step_returns_not_wired():
    """Steps without adapter and non-dispatch return not_wired status."""
    executor = get_step_executor()
    step = _step("domain_cluster")
    result = executor.execute(step, artifacts={}, context={})
    assert result.status == "not_wired"
    assert result.executable is False
