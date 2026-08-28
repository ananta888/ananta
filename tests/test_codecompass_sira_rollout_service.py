from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.codecompass_sira_rollout_service import (
    CodeCompassSiraRolloutService,
    SiraRolloutObservation,
    SiraRolloutPolicy,
)


def _evaluation_policy():
    return {
        "schema": "codecompass.sira-evaluation-policy.v1",
        "minimum_verified_queries": 1,
        "minimum_repository_count": 1,
        "minimum_aggregate_delta": {
            "recall": 0.0,
            "ndcg": 0.0,
            "mrr": 0.0,
            "evidence_coverage": 0.0,
        },
        "minimum_delta_ci95_lower": {
            "recall": 0.0,
            "ndcg": 0.0,
            "mrr": 0.0,
            "evidence_coverage": 0.0,
        },
        "protected_query_classes": ["security"],
        "maximum_protected_class_regression": 0.0,
        "efficiency_budgets": {"p95_latency_ms": 100},
    }


def _evaluation_report(*, passed: bool = True):
    delta = 0.1 if passed else -0.1
    metric = {
        "baseline": 0.5,
        "candidate": 0.5 + delta,
        "delta": delta,
        "delta_ci95": {"lower": delta, "upper": delta},
    }
    metrics = {
        "recall_at_10": metric,
        "ndcg_at_10": metric,
        "mrr": metric,
        "evidence_coverage": metric,
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
        "verified_query_count": 1,
        "repositories": {"repo": {"verified_query_count": 1, **metrics}},
        "query_classes": {"security": {"verified_query_count": 1, **metrics}},
        "aggregate": metrics,
        "efficiency": {"candidate": {"p95_latency_ms": 10}},
    }


def _admit(service):
    return service.admit_benchmark(
        scope_id="tenant:project",
        report=_evaluation_report(),
        evaluation_policy=_evaluation_policy(),
    )


def _observation(identifier: str, stage: str):
    return SiraRolloutObservation(
        observation_id=identifier,
        stage=stage,
        success=True,
        quality_delta=0.1,
        latency_ms=10,
        tokens=10,
        cost=0.0,
    )


def test_rollout_advances_shadow_canary_preferred_without_human_gate(tmp_path):
    service = CodeCompassSiraRolloutService(
        tmp_path / "rollout.sqlite3",
        policy=SiraRolloutPolicy(
            minimum_shadow_observations=2,
            minimum_canary_observations=2,
            canary_basis_points=1000,
        ),
    )
    assert _admit(service)["stage"] == "shadow"
    profile = service.retrieval_profile(
        scope_id="tenant:project",
        request_id="shadow-request",
        corpus_ready=True,
    )
    assert profile["name"] == "corpus_discriminative_lexical"
    assert profile["rollout"] == {
        "schema": "codecompass.sira-rollout-decision.v1",
        "stage": "shadow",
        "result_affecting": False,
        "reason_code": "sira_shadow_non_effecting",
        "revision": 1,
    }
    service.observe(scope_id="tenant:project", observation=_observation("shadow-1", "shadow"))
    state = service.observe(scope_id="tenant:project", observation=_observation("shadow-2", "shadow"))
    assert state["stage"] == "canary"

    first = service.assignment(scope_id="tenant:project", request_id="request-1")
    assert first == service.assignment(scope_id="tenant:project", request_id="request-1")
    assert first.stage == "canary"

    service.observe(scope_id="tenant:project", observation=_observation("canary-1", "canary"))
    state = service.observe(scope_id="tenant:project", observation=_observation("canary-2", "canary"))
    assert state["stage"] == "preferred"
    assert service.assignment(scope_id="tenant:project", request_id="request-2").result_affecting is True


def test_rollout_stops_automatically_and_persists_on_any_security_regression(tmp_path):
    path = tmp_path / "rollout.sqlite3"
    service = CodeCompassSiraRolloutService(
        path,
        policy=SiraRolloutPolicy(minimum_shadow_observations=2, minimum_canary_observations=2),
    )
    _admit(service)

    state = service.observe(
        scope_id="tenant:project",
        observation=replace(_observation("unsafe", "shadow"), security_regression=True),
    )

    assert state["stage"] == "off"
    assert state["reason_code"] == "sira_security_regression"
    restored = CodeCompassSiraRolloutService(
        path,
        policy=SiraRolloutPolicy(minimum_shadow_observations=2, minimum_canary_observations=2),
    )
    assert restored.snapshot(scope_id="tenant:project")["reason_code"] == "sira_security_regression"
    assert restored.assignment(scope_id="tenant:project", request_id="request").result_affecting is False


def test_failed_benchmark_keeps_rollout_off(tmp_path):
    service = CodeCompassSiraRolloutService(tmp_path / "rollout.sqlite3")

    state = service.admit_benchmark(
        scope_id="tenant:project",
        report=_evaluation_report(passed=False),
        evaluation_policy=_evaluation_policy(),
    )

    assert state["stage"] == "off"
    assert state["reason_code"] == "sira_benchmark_gate_failed"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"index_compatible": False}, "sira_index_incompatible"),
        ({"model_available": False}, "sira_model_unavailable"),
        ({"delta_complete": False}, "sira_partial_delta"),
    ],
)
def test_rollout_automatically_rolls_back_incompatible_runtime_conditions(tmp_path, changes, reason):
    service = CodeCompassSiraRolloutService(tmp_path / "rollout.sqlite3")
    _admit(service)

    state = service.observe(
        scope_id="tenant:project",
        observation=replace(_observation(reason, "shadow"), **changes),
    )

    assert state["stage"] == "off"
    assert state["reason_code"] == reason
