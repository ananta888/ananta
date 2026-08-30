"""Deterministic dependency-free backend for bounded automatic tests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ananta_contracts.research_training import STAGE_CAPABILITIES, canonical_json
from worker.training.research.backend import ResearchStageOutput

_ARTIFACT_BY_STAGE = {
    "tokenizer_train": "tokenizer",
    "tokenizer_eval": "tokenizer_report",
    "pretrain": "base_checkpoint",
    "base_eval": "base_evaluation",
    "sft": "sft_checkpoint",
    "chat_eval": "chat_evaluation",
    "rl": "rl_checkpoint",
    "rl_eval": "rl_evaluation",
    "inference_benchmark": "inference_benchmark",
    "export": "model_export",
}


class DeterministicResearchMockBackend:
    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(STAGE_CAPABILITIES.values())

    def execute(
        self,
        *,
        run_spec: Mapping[str, Any],
        stage: Mapping[str, Any],
        attempt_id: str,
    ) -> ResearchStageOutput:
        kind = str(stage.get("kind") or "")
        if kind not in _ARTIFACT_BY_STAGE:
            raise ValueError("research_mock_stage_kind_invalid")
        seed_material = canonical_json(
            {
                "spec_digest": hashlib.sha256(canonical_json(run_spec).encode()).hexdigest(),
                "stage_id": stage.get("stage_id"),
                "kind": kind,
                "attempt_id": attempt_id,
            }
        )
        scalar = int(hashlib.sha256(seed_material.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        metrics = {
            "loss": round(0.3 + scalar * 0.2, 6),
            "accuracy": round(0.7 + scalar * 0.2, 6),
            "latency_ms": round(2 + scalar * 5, 6),
            "throughput_tokens_s": round(100 + scalar * 50, 6),
            "peak_memory_bytes": float(1_048_576 + int(scalar * 1_048_576)),
        }
        content = canonical_json(
            {
                "schema": "ananta.research-training-mock-artifact.v1",
                "stage_id": stage.get("stage_id"),
                "stage_kind": kind,
                "attempt_id": attempt_id,
                "metrics": metrics,
                "synthetic": True,
                "claims_verified": False,
            }
        ).encode()
        return ResearchStageOutput(
            artifact_kind=_ARTIFACT_BY_STAGE[kind],
            content=content,
            metrics=metrics,
            executable=False,
        )


__all__ = ["DeterministicResearchMockBackend"]
