"""Tests for DelegationTraceService (TRANS-005)."""
from __future__ import annotations

from agent.services.delegation_trace_service import (
    DelegationTrace,
    DelegationTraceService,
    WorkerAlternative,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc() -> DelegationTraceService:
    return DelegationTraceService()


def _basic_trace(svc: DelegationTraceService, *, run_id: str = "run-dt-001") -> DelegationTrace:
    return svc.record(
        run_id=run_id,
        goal_summary="Implement feature X",
        chosen_worker_id="worker-python-01",
        selection_reason="highest_capability_score",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_record_basic() -> None:
    """record() must generate a trace_id and store chosen_worker_id correctly."""
    svc = _svc()
    trace = _basic_trace(svc)

    assert trace.trace_id != ""
    assert len(trace.trace_id) == 36  # UUID format
    assert trace.chosen_worker_id == "worker-python-01"
    assert trace.run_id == "run-dt-001"
    assert trace.goal_summary == "Implement feature X"
    assert trace.selection_reason == "highest_capability_score"


def test_alternatives_tracked() -> None:
    """Alternatives not chosen must be stored with their rejection reason."""
    svc = _svc()
    alts = [
        {"worker_id": "worker-js-01", "reason_not_chosen": "missing_capability", "score": 0.3},
        {"worker_id": "worker-py-02", "reason_not_chosen": "lower_score", "score": 0.7},
        {"worker_id": "worker-rust-01", "reason_not_chosen": "denied_provider", "score": None},
    ]
    trace = svc.record(
        run_id="run-alts",
        goal_summary="Fix bug Y",
        chosen_worker_id="worker-py-01",
        selection_reason="best_match",
        alternatives=alts,
    )

    assert len(trace.alternatives_considered) == 3

    reasons = {a.worker_id: a.reason_not_chosen for a in trace.alternatives_considered}
    assert reasons["worker-js-01"] == "missing_capability"
    assert reasons["worker-py-02"] == "lower_score"
    assert reasons["worker-rust-01"] == "denied_provider"

    scores = {a.worker_id: a.score for a in trace.alternatives_considered}
    assert scores["worker-js-01"] == 0.3
    assert scores["worker-rust-01"] is None


def test_alternatives_invalid_reason_defaults_to_unavailable() -> None:
    """Unknown reason_not_chosen values are normalized to 'unavailable'."""
    svc = _svc()
    trace = svc.record(
        run_id="run-x",
        goal_summary="g",
        chosen_worker_id="w1",
        selection_reason="r",
        alternatives=[{"worker_id": "w2", "reason_not_chosen": "UNKNOWN_REASON", "score": None}],
    )
    assert trace.alternatives_considered[0].reason_not_chosen == "unavailable"


def test_summarize() -> None:
    """summarize() must return a non-empty one-liner without raising."""
    svc = _svc()
    trace = svc.record(
        run_id="run-sum",
        goal_summary="Deploy service",
        chosen_worker_id="worker-deploy-01",
        selection_reason="only_capable_worker",
        tools_granted=["kubectl_apply", "helm_upgrade"],
        alternatives=[{"worker_id": "w2", "reason_not_chosen": "too_risky", "score": 0.5}],
    )
    summary = svc.summarize(trace)

    assert isinstance(summary, str)
    assert len(summary) > 0
    assert "worker-deploy-01" in summary
    assert "run-sum" in summary


def test_to_dict() -> None:
    """to_dict() must return a plain dict containing all DelegationTrace fields."""
    svc = _svc()
    trace = svc.record(
        run_id="run-dict",
        goal_summary="Analyse logs",
        chosen_worker_id="worker-analysis",
        selection_reason="best_fit",
        chosen_expert_id="expert-logs",
        policy_scope_id="scope-prod",
        context_provided=["artifact-a1", "artifact-a2"],
        tools_granted=["read_logs"],
    )
    d = svc.to_dict(trace)

    assert isinstance(d, dict)
    assert d["trace_id"] == trace.trace_id
    assert d["run_id"] == "run-dict"
    assert d["chosen_worker_id"] == "worker-analysis"
    assert d["chosen_expert_id"] == "expert-logs"
    assert d["policy_scope_id"] == "scope-prod"
    assert d["context_provided"] == ["artifact-a1", "artifact-a2"]
    assert d["tools_granted"] == ["read_logs"]
    assert isinstance(d["alternatives_considered"], list)
    assert "created_at" in d


def test_tools_granted_explicit() -> None:
    """tools_granted must be an empty list (not None) when no tools are passed."""
    svc = _svc()
    trace = svc.record(
        run_id="run-tools",
        goal_summary="Read-only analysis",
        chosen_worker_id="worker-ro",
        selection_reason="read_only_task",
        tools_granted=None,
    )
    assert trace.tools_granted == []
    assert isinstance(trace.tools_granted, list)


def test_context_provided_defaults_to_empty() -> None:
    """context_provided must be an empty list when not specified."""
    svc = _svc()
    trace = _basic_trace(svc)
    assert trace.context_provided == []
    assert isinstance(trace.context_provided, list)


def test_policy_scope_id_optional() -> None:
    """policy_scope_id may be None."""
    svc = _svc()
    trace = _basic_trace(svc)
    assert trace.policy_scope_id is None


def test_trace_ids_are_unique() -> None:
    """Each trace gets a unique trace_id."""
    svc = _svc()
    ids = {_basic_trace(svc).trace_id for _ in range(5)}
    assert len(ids) == 5
