"""Deterministic quality and runtime gates for research checkpoints."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.research_training_evaluation_attestation import ResearchTrainingEvaluationAttestation
from ananta_contracts.research_training import canonical_digest, require_digest, require_id


class ResearchTrainingEvaluationService:
    METRICS = frozenset({"loss", "accuracy", "latency_ms", "throughput_tokens_s", "peak_memory_bytes"})

    def __init__(
        self,
        attestations: ResearchTrainingEvaluationAttestation,
        *,
        allowed_source_refs: Sequence[str] = (),
        allowed_run_refs: Sequence[str] = (),
    ) -> None:
        self._attestations = attestations
        self._source_refs = frozenset(allowed_source_refs)
        self._run_refs = frozenset(allowed_run_refs)

    def compare(
        self,
        *,
        run_id: str,
        dataset_manifest_digest: str,
        base: Mapping[str, Any],
        sft: Mapping[str, Any],
        inference: Mapping[str, Any],
        rl: Mapping[str, Any] | None = None,
        source_refs: Sequence[str] = (),
        run_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        normalized = {
            "base": self._metrics(base),
            "sft": self._metrics(sft),
            "inference": self._metrics(inference),
        }
        if rl is not None:
            normalized["rl"] = self._metrics(rl)
        reasons: list[str] = []
        if normalized["sft"]["accuracy"] < normalized["base"]["accuracy"]:
            reasons.append("research_sft_accuracy_regression")
        if normalized["sft"]["loss"] > normalized["base"]["loss"]:
            reasons.append("research_sft_loss_regression")
        if normalized["inference"]["throughput_tokens_s"] <= 0:
            reasons.append("research_inference_throughput_invalid")
        if rl is not None and normalized["rl"]["accuracy"] < normalized["sft"]["accuracy"]:
            reasons.append("research_rl_accuracy_regression")
        checked_sources = self._refs(source_refs, "SRC_", self._source_refs, "source")
        checked_runs = self._refs(run_refs, "RUN_", self._run_refs, "run")
        if not checked_sources:
            reasons.append("research_source_evidence_missing")
        if not checked_runs:
            reasons.append("research_run_evidence_missing")
        result = {
            "schema": "ananta.research-training-evaluation.v1",
            "run_id": require_id(run_id, "run_id"),
            "dataset_manifest_digest": require_digest(dataset_manifest_digest, "dataset_manifest_digest"),
            "metrics": normalized,
            "source_refs": checked_sources,
            "run_refs": checked_runs,
            "release_eligible": not reasons,
            "reason_codes": sorted(set(reasons)),
            "production_eligible": False,
            "claims_verified": bool(checked_sources and checked_runs),
            "human_intervention_required": False,
        }
        result["evaluation_digest"] = canonical_digest(result)
        result["attestation"] = self._attestations.issue(result)
        return result

    def verify(self, result: Mapping[str, Any]) -> bool:
        return self._attestations.verify(result)

    def _metrics(self, value: Mapping[str, Any]) -> dict[str, float]:
        if set(value) != self.METRICS:
            raise ValueError("research_evaluation_metric_fields_invalid")
        result = {key: float(value[key]) for key in self.METRICS}
        if any(not math.isfinite(item) or item < 0 for item in result.values()):
            raise ValueError("research_evaluation_metric_invalid")
        if result["accuracy"] > 1:
            raise ValueError("research_evaluation_accuracy_invalid")
        return result

    @staticmethod
    def _refs(values: Sequence[str], prefix: str, allowed: frozenset[str], field: str) -> list[str]:
        normalized = sorted({str(value).strip() for value in values if str(value).strip()})
        if any(not value.startswith(prefix) for value in normalized):
            raise ValueError(f"research_{field}_ref_invalid")
        if any(value not in allowed for value in normalized):
            raise ValueError(f"research_{field}_ref_unknown")
        return normalized


__all__ = ["ResearchTrainingEvaluationService"]
