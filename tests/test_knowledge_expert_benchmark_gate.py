from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.knowledge_expert_benchmark_gate import KnowledgeExpertBenchmarkGate

ROOT = Path(__file__).resolve().parents[1]


def _runs():
    bindings = {
        "model_digest": "a" * 64,
        "tokenizer_digest": "b" * 64,
        "dataset_digest": "c" * 64,
        "hardware_digest": "d" * 64,
        "software_digest": "e" * 64,
    }
    return [
        {
            **bindings,
            "variant": variant,
            "quality_score": 0.7 if variant == "dense" else 0.72,
            "general_holdout_score": 0.9,
            "security_holdout_score": 1.0,
            "retrieval_ms": 1,
            "cold_load_ms": 2,
            "warm_load_ms": 1,
            "hot_switch_ms": 1,
            "inference_ms": 4,
        }
        for variant in (
            "dense",
            "rag",
            "sft_lora",
            "single_expert",
            "multi_expert",
            "expert_plus_rag",
            "retrieval_only_router",
            "always_on_router",
            "uncertainty_router",
        )
    ]


def _gate():
    config = json.loads((ROOT / "config/benchmarks/knowledge-experts.v1.json").read_text())
    return KnowledgeExpertBenchmarkGate(config)


def test_benchmark_gate_requires_bound_complete_ablation_matrix():
    result = _gate().evaluate(_runs())
    assert result["passed"] is True
    assert result["reason_code"] == "benchmark_promotion_passed"


def test_benchmark_gate_blocks_binding_mismatch_and_security_regression():
    mismatched = _runs()
    mismatched[-1]["model_digest"] = "f" * 64
    assert _gate().evaluate(mismatched)["reason_code"] == "benchmark_binding_mismatch"
    regressed = _runs()
    regressed[-1]["security_holdout_score"] = 0.9
    assert _gate().evaluate(regressed)["reason_code"] == "benchmark_security_regression"


def test_benchmark_gate_fails_closed_on_ambiguous_or_non_finite_reports():
    duplicated = _runs()
    duplicated.append(dict(duplicated[-1]))
    assert _gate().evaluate(duplicated)["reason_code"] == "benchmark_variant_duplicate"

    unexpected = _runs()
    unexpected[-1]["variant"] = "client_invented"
    assert _gate().evaluate(unexpected)["reason_code"] == "benchmark_variants_unexpected"

    non_finite = _runs()
    non_finite[-1]["quality_score"] = float("nan")
    assert _gate().evaluate(non_finite) == {
        "schema": "ananta.knowledge-expert-benchmark-gate.v1",
        "passed": False,
        "reason_code": "benchmark_metric_invalid",
        "details": ["uncertainty_router", "quality_score"],
    }


def test_benchmark_gate_rejects_unknown_config_versions_and_dense_only_matrix():
    config = json.loads((ROOT / "config/benchmarks/knowledge-experts.v1.json").read_text())
    config["version"] = "2.0.0"
    with pytest.raises(ValueError, match="benchmark_config_invalid"):
        KnowledgeExpertBenchmarkGate(config)

    config["version"] = "1.0.0"
    config["required_variants"] = ["dense"]
    with pytest.raises(ValueError, match="benchmark_config_invalid"):
        KnowledgeExpertBenchmarkGate(config)
