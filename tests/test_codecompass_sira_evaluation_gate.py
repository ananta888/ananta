from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.codecompass_sira_evaluation_gate import CodeCompassSiraEvaluationGate


def _policy():
    return {
        "schema": "codecompass.sira-evaluation-policy.v1",
        "minimum_verified_queries": 2,
        "minimum_repository_count": 2,
        "minimum_aggregate_delta": {
            "recall": 0.01,
            "ndcg": 0.01,
            "mrr": 0.0,
            "evidence_coverage": 0.01,
        },
        "minimum_delta_ci95_lower": {
            "recall": 0.0,
            "ndcg": 0.0,
            "mrr": 0.0,
            "evidence_coverage": 0.0,
        },
        "protected_query_classes": ["exact_symbol", "security"],
        "maximum_protected_class_regression": 0.0,
        "efficiency_budgets": {
            "lexical_retrieval_calls_per_query": 1,
            "p95_latency_ms": 100,
        },
    }


def _metric(delta=0.1):
    return {
        "baseline": 0.5,
        "candidate": 0.5 + delta,
        "delta": delta,
        "delta_ci95": {"lower": delta, "upper": delta},
    }


def _report():
    metrics = {
        "recall_at_10": _metric(),
        "ndcg_at_10": _metric(),
        "mrr": _metric(),
        "evidence_coverage": _metric(),
    }
    return {
        "binding": {
            "repository_revision": "revision",
            "source_manifest_hash": "manifest",
            "golden_digest": "golden",
            "model_digest": "model",
            "prompt_digest": "prompt",
            "index_digest": "index",
        },
        "verified_query_count": 2,
        "repositories": {"repo-a": metrics, "repo-b": metrics},
        "query_classes": {
            "exact_symbol": {"verified_query_count": 1, **metrics},
            "security": {"verified_query_count": 1, **metrics},
        },
        "aggregate": metrics,
        "efficiency": {
            "candidate": {
                "lexical_retrieval_calls_per_query": 1,
                "p95_latency_ms": 80,
            }
        },
    }


def test_gate_passes_only_complete_bound_quality_and_efficiency_report():
    decision = CodeCompassSiraEvaluationGate().assess(_report(), _policy())

    assert decision.passed is True
    assert decision.reason_codes == ("sira_evaluation_gate_passed",)
    assert len(decision.policy_sha256) == 64


def test_gate_fails_closed_on_protected_regression_and_unmeasured_budget():
    report = _report()
    report["query_classes"]["security"]["recall_at_10"] = _metric(-0.1)
    report["efficiency"]["candidate"].pop("p95_latency_ms")

    decision = CodeCompassSiraEvaluationGate().assess(report, _policy())

    assert decision.passed is False
    assert "sira_gate_protected_class_regression:security:recall" in decision.reason_codes
    assert "sira_gate_efficiency_unverified:p95_latency_ms" in decision.reason_codes


def test_gate_rejects_malformed_policy_as_configuration_error():
    policy = _policy()
    policy["protected_query_classes"] = "security"

    with pytest.raises(ValueError, match="sira_evaluation_policy_invalid"):
        CodeCompassSiraEvaluationGate().assess(_report(), policy)


def test_production_policy_matches_checked_schema():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas/codecompass.sira-evaluation-policy.v1.json").read_text())
    policy = json.loads((root / "config/retrieval/codecompass-sira-evaluation-policy.v1.json").read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(policy)
