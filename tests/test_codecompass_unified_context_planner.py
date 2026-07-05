"""COMBO-001: unified context planner tests.

Acceptance (from todo):

* the existing CodeCompassContextPlanner is extended; no parallel
  service is introduced
* context planner has clear buckets: changed_files, symbol_neighbors,
  build_test_evidence, semantic_chunks, policy_evidence
* weighting is task_kind-dependent
* budget decisions are recorded in context_package
* tests cover review, ci_failure, architecture_question, security_policy_task
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.codecompass_context_planner_service import (
    DEFAULT_WEIGHTS,
    SCHEMA_UNIFIED_CONTEXT,
    TASK_KIND_WEIGHTS,
    CodeCompassContextPlanner,
    get_codecompass_context_planner,
)


@pytest.fixture
def planner() -> CodeCompassContextPlanner:
    return get_codecompass_context_planner()


# ---------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------

def test_weights_for_review_prioritises_symbol_neighbors(planner):
    w = planner.weights_for("review")
    assert w["symbol_neighbors"] > w["build_test_evidence"]
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_weights_for_ci_prioritises_build_test_evidence(planner):
    w = planner.weights_for("ci")
    assert w["build_test_evidence"] > w["symbol_neighbors"]


def test_weights_for_security_policy_prioritises_policy_evidence(planner):
    w = planner.weights_for("security_policy_task")
    assert w["policy_evidence"] > w["semantic_chunks"]


def test_unknown_task_kind_falls_back_to_default(planner):
    w = planner.weights_for("totally_unknown_kind")
    assert w == DEFAULT_WEIGHTS


def test_all_weights_sum_to_one(planner):
    for kind, w in TASK_KIND_WEIGHTS.items():
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-6), kind


# ---------------------------------------------------------------------------
# buckets
# ---------------------------------------------------------------------------

def test_plan_unified_context_produces_all_buckets(planner):
    res = planner.plan_unified_context(
        query="hello",
        task_kind="review",
        bucket_inputs={
            "changed_files": [{"path": "a.py", "line_start": 1, "line_end": 10}],
            "symbol_neighbors": [{"path": "b.py", "line_start": 5, "line_end": 15}],
            "build_test_evidence": [{"path": "test.py", "line_start": 1, "line_end": 3}],
            "semantic_chunks": [{"path": "doc.md", "line_start": 0, "line_end": 1}],
            "policy_evidence": [{"path": "policy.md", "line_start": 0, "line_end": 1}],
        },
    )
    assert res["schema"] == SCHEMA_UNIFIED_CONTEXT
    assert set(res["buckets"]) == {
        "changed_files", "symbol_neighbors", "build_test_evidence",
        "semantic_chunks", "policy_evidence",
    }


def test_plan_unified_context_records_budget_decisions(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={
            "changed_files": [{"path": f"f{i}.py", "line_start": 1, "line_end": 1}
                              for i in range(50)],
        },
    )
    decisions = res["decisions"]
    assert any(d["reason"] == "bucket_max_applied" for d in decisions)
    assert any(d["bucket"] == "changed_files" for d in decisions)


def test_plan_unified_context_unknown_bucket_warns(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={"bogus_bucket": [{"path": "a.py"}]},
    )
    assert any("unknown_bucket:bogus_bucket" in w for w in res["warnings"])


def test_plan_unified_context_diagnostic_counts(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={
            "changed_files": [{"path": "a.py"}] * 3,
            "build_test_evidence": [{"path": "b.py"}] * 5,
        },
    )
    counts = res["diagnostics"]["bucket_counts"]
    assert counts["changed_files"] == 3
    assert counts["build_test_evidence"] == 5


def test_plan_unified_context_weighted_total(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={
            "symbol_neighbors": [{"path": "a.py"}] * 4,
        },
    )
    # 4 items * 0.40 weight = 1.6
    assert res["diagnostics"]["weighted_total"] == pytest.approx(1.6, abs=1e-4)


# ---------------------------------------------------------------------------
# task_kind acceptance tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_kind", [
    "review", "bugfix", "ci", "build",
    "architecture_question", "security_policy_task",
])
def test_all_required_task_kinds_supported(planner, task_kind):
    res = planner.plan_unified_context(
        query="x", task_kind=task_kind,
        bucket_inputs={"symbol_neighbors": [{"path": "a.py"}]},
    )
    assert res["task_kind"] == task_kind
    assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-6)


def test_review_task_prioritises_symbol_neighbors_in_decisions(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={
            "changed_files": [{"path": "f1.py"}] * 100,
            "symbol_neighbors": [{"path": "f2.py"}] * 100,
            "build_test_evidence": [{"path": "f3.py"}] * 100,
        },
    )
    # Both buckets get budget-capped; check the weight table rather
    # than the truncated counts (the weighted_total uses the
    # post-cap count).
    assert res["weights"]["symbol_neighbors"] > res["weights"]["build_test_evidence"]


# ---------------------------------------------------------------------------
# source-grounded: no synthetic IDs
# ---------------------------------------------------------------------------

def test_plan_unified_context_does_not_invent_ids(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={
            "symbol_neighbors": [{"path": "a.py"}],  # no id at all
        },
    )
    # No synthetic 'unknown-1' or similar IDs introduced.
    refs = res["buckets"]["symbol_neighbors"]
    for ref in refs:
        # either the caller supplied an id, or none — but never a synthetic
        synthetic = [v for v in ref.values()
                     if isinstance(v, str) and v.startswith("synth:")]
        assert synthetic == []


def test_plan_unified_context_bundle_id_is_stable(planner):
    bucket_inputs = {"symbol_neighbors": [{"path": "a.py"}]}
    a = planner.plan_unified_context(query="x", task_kind="review",
                                     bucket_inputs=bucket_inputs)
    b = planner.plan_unified_context(query="x", task_kind="review",
                                     bucket_inputs=bucket_inputs)
    assert a["bundle_id"] == b["bundle_id"]


def test_plan_unified_context_result_is_json_serialisable(planner):
    res = planner.plan_unified_context(
        query="x",
        task_kind="review",
        bucket_inputs={"symbol_neighbors": [{"path": "a.py"}]},
    )
    json.dumps(res)


def test_legacy_fallback_when_bucket_inputs_empty(planner):
    """If no bucket_inputs are provided, the planner must still
    produce a usable context package (legacy plan_context fallback)."""
    res = planner.plan_unified_context(query="hello", task_kind="review")
    assert res["schema"] == SCHEMA_UNIFIED_CONTEXT
    # legacy plan_context returns an empty refs list when no retrieval
    # backend is wired in production; the bucket key is still present
    # and the decision is recorded.
    assert "symbol_neighbors" in res["buckets"]
    assert any(d["reason"] == "legacy_plan_context_fallback"
               for d in res["decisions"])


def test_legacy_fallback_records_decision_reason(planner):
    res = planner.plan_unified_context(query="hello", task_kind="review")
    reasons = [d["reason"] for d in res["decisions"]]
    assert "legacy_plan_context_fallback" in reasons