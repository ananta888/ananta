from __future__ import annotations

from worker.retrieval.codecompass_budgeting import apply_codecompass_budget, resolve_codecompass_budget


def test_codecompass_budget_profiles_have_distinct_caps():
    safe = resolve_codecompass_budget(profile="safe")
    balanced = resolve_codecompass_budget(profile="balanced")
    fast = resolve_codecompass_budget(profile="fast")

    assert safe["max_total_chunks"] < balanced["max_total_chunks"] <= fast["max_total_chunks"]
    assert safe["channel_caps"]["codecompass_fts"] >= safe["channel_caps"]["codecompass_vector"]
    assert balanced["strategy"] == "hybrid_bounded"


def test_codecompass_budgeting_reports_degraded_reason_when_budget_exhausted():
    ranked = [
        {"channel": "codecompass_fts", "final_score": 0.9},
        {"channel": "codecompass_fts", "final_score": 0.8},
        {"channel": "codecompass_fts", "final_score": 0.7},
        {"channel": "codecompass_vector", "final_score": 0.6},
        {"channel": "codecompass_vector", "final_score": 0.5},
        {"channel": "codecompass_graph", "final_score": 0.4},
        {"channel": "codecompass_graph", "final_score": 0.3},
    ]
    payload = apply_codecompass_budget(ranked_candidates=ranked, profile="safe", top_k=10)

    selected = payload["selected"]
    budget = payload["budget"]
    assert len(selected) <= budget["max_total_chunks"]
    assert budget["degraded_reason"] == "budget_exhausted"
    assert budget["dropped_by_reason"]["total_budget"] >= 1 or budget["dropped_by_reason"]["channel_cap"] >= 1
