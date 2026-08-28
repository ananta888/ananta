from __future__ import annotations

import json
from pathlib import Path

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
