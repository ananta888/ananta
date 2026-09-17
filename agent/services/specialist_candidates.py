"""Base-model candidate comparison and artifact lineage (GBMF-005 / GBMF-006).

* ``CandidatePlan`` pins one dataset digest and one comparable training
  budget for every base-model candidate; no model family is hard-wired.
* ``compare_candidates`` ranks benchmarked candidates: the cheapest/smallest
  candidate wins whenever it clears the quality and safety thresholds.
* ``ArtifactLineage`` binds base model, adapter, dataset, job and optional
  merge/export so every released artifact is traceable; exports derived from
  an unevaluated adapter are never promotable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.specialist_decision import canonical_digest
from agent.services.specialist_benchmark import BenchmarkResult

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$")
EXPORT_FORMATS = frozenset({"adapter", "merged_16bit", "gguf"})


class SpecialistCandidateError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


# --- GBMF-005 ---------------------------------------------------------------


@dataclass(frozen=True)
class TrainingBudget:
    max_steps: int
    max_seconds: int
    max_vram_bytes: int

    def __post_init__(self) -> None:
        for value in (self.max_steps, self.max_seconds, self.max_vram_bytes):
            if type(value) is not int or value <= 0:
                raise SpecialistCandidateError("specialist_budget_invalid")


@dataclass(frozen=True)
class BaseModelCandidate:
    base_model: str
    method: str  # lora | qlora, chosen per candidate size
    parameter_count: int

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.base_model) or self.method not in {"lora", "qlora"}:
            raise SpecialistCandidateError("specialist_candidate_invalid")
        if type(self.parameter_count) is not int or self.parameter_count <= 0:
            raise SpecialistCandidateError("specialist_candidate_invalid")

    @property
    def candidate_id(self) -> str:
        return f"{self.base_model}:{self.method}"


def choose_method(parameter_count: int, *, lora_max_parameters: int = 2_000_000_000) -> str:
    """QLoRA for larger candidates, LoRA where the model loads efficiently at full precision."""
    return "lora" if parameter_count <= lora_max_parameters else "qlora"


@dataclass(frozen=True)
class CandidatePlan:
    specialist_id: str
    dataset_digest: str
    holdout_digest: str
    benchmark_version: str
    budget: TrainingBudget
    candidates: tuple[BaseModelCandidate, ...]

    def __post_init__(self) -> None:
        if len(self.candidates) < 2 or len({c.candidate_id for c in self.candidates}) != len(self.candidates):
            raise SpecialistCandidateError("specialist_candidates_require_two_base_models")
        for digest in (self.dataset_digest, self.holdout_digest):
            if not _DIGEST.fullmatch(digest):
                raise SpecialistCandidateError("specialist_candidate_digest_invalid")

    @property
    def plan_digest(self) -> str:
        return canonical_digest(
            {
                "specialist_id": self.specialist_id,
                "dataset_digest": self.dataset_digest,
                "holdout_digest": self.holdout_digest,
                "benchmark_version": self.benchmark_version,
                "budget": self.budget.__dict__,
                "candidates": [c.candidate_id for c in self.candidates],
            }
        )

    def hyperparameters(self) -> dict[str, Any]:
        """Same bounded budget for every candidate (comparable runs)."""
        return {"max_steps": self.budget.max_steps}


@dataclass(frozen=True)
class QualityThresholds:
    min_accuracy: float
    min_macro_f1: float
    max_false_allow_rate: float | None = None
    max_false_deny_rate: float | None = None
    max_invalid_output_rate: float = 0.05

    def violations(self, result: BenchmarkResult) -> list[str]:
        problems = []
        if result.classification["accuracy"] < self.min_accuracy:
            problems.append("accuracy_below_minimum")
        if result.classification["macro_f1"] < self.min_macro_f1:
            problems.append("macro_f1_below_minimum")
        if result.classification["invalid_output_rate"] > self.max_invalid_output_rate:
            problems.append("invalid_output_rate_above_maximum")
        if result.safety.get("gate_type"):
            for name, limit in (("false_allow_rate", self.max_false_allow_rate), ("false_deny_rate", self.max_false_deny_rate)):
                rate = result.safety.get(name)
                if limit is not None and rate is not None and rate > limit:
                    problems.append(f"{name}_above_maximum")
        return problems


@dataclass(frozen=True)
class CandidateRanking:
    winner: str | None
    ranked: tuple[str, ...]
    eligible: tuple[str, ...]
    violations: Mapping[str, tuple[str, ...]]
    plan_digest: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "ranked": list(self.ranked),
            "eligible": list(self.eligible),
            "violations": {key: list(value) for key, value in self.violations.items()},
            "plan_digest": self.plan_digest,
        }


def compare_candidates(
    plan: CandidatePlan,
    results: Sequence[BenchmarkResult],
    thresholds: QualityThresholds,
) -> CandidateRanking:
    """Cheapest eligible candidate wins; ineligible ones keep their violation list."""
    by_id = {result.candidate_id: result for result in results}
    expected = {candidate.candidate_id for candidate in plan.candidates}
    if set(by_id) != expected:
        raise SpecialistCandidateError("specialist_candidate_results_incomplete")
    for result in results:
        if result.holdout_digest != plan.holdout_digest or result.benchmark_version != plan.benchmark_version:
            raise SpecialistCandidateError("specialist_candidate_benchmark_mismatch")
    violations = {cid: tuple(thresholds.violations(result)) for cid, result in by_id.items()}
    eligible = [cid for cid, problems in violations.items() if not problems]

    def cost(candidate_id: str) -> tuple[float, float, float]:
        result = by_id[candidate_id]
        params = next(c.parameter_count for c in plan.candidates if c.candidate_id == candidate_id)
        latency = result.resources.get("latency_ms_p50") or 0.0
        return (float(params), float(latency), -float(result.classification["macro_f1"]))

    ranked = sorted(eligible, key=cost) + sorted(cid for cid in by_id if cid not in eligible)
    return CandidateRanking(
        winner=ranked[0] if eligible else None,
        ranked=tuple(ranked),
        eligible=tuple(sorted(eligible, key=cost)),
        violations=violations,
        plan_digest=plan.plan_digest,
    )


# --- GBMF-006 ---------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactLineage:
    """Provenance of one adapter (primary artifact) and its optional exports."""

    specialist_id: str
    contract_version: str
    version: str  # specialist version, e.g. v1, v2
    base_model: str
    base_model_digest: str
    adapter_digest: str
    dataset_digest: str
    holdout_digest: str
    training_job_id: str
    method: str
    evaluated: bool = False
    benchmark_result_digest: str | None = None
    exports: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for digest in (self.base_model_digest, self.adapter_digest, self.dataset_digest, self.holdout_digest):
            if not _DIGEST.fullmatch(digest):
                raise SpecialistCandidateError("specialist_lineage_digest_invalid")
        if not _ID.fullmatch(self.training_job_id) or self.method not in {"lora", "qlora"}:
            raise SpecialistCandidateError("specialist_lineage_invalid")

    @property
    def lineage_digest(self) -> str:
        return canonical_digest(self.to_mapping(include_exports=False))

    def to_mapping(self, *, include_exports: bool = True) -> dict[str, Any]:
        body = {
            "specialist_id": self.specialist_id,
            "contract_version": self.contract_version,
            "version": self.version,
            "base_model": self.base_model,
            "base_model_digest": self.base_model_digest,
            "adapter_digest": self.adapter_digest,
            "dataset_digest": self.dataset_digest,
            "holdout_digest": self.holdout_digest,
            "training_job_id": self.training_job_id,
            "method": self.method,
            "evaluated": self.evaluated,
            "benchmark_result_digest": self.benchmark_result_digest,
        }
        if include_exports:
            body["exports"] = [dict(item) for item in self.exports]
        return body

    def with_evaluation(self, result: BenchmarkResult) -> "ArtifactLineage":
        if result.specialist_id != self.specialist_id or result.holdout_digest != self.holdout_digest:
            raise SpecialistCandidateError("specialist_lineage_benchmark_mismatch")
        return ArtifactLineage(**{**self.__dict__, "evaluated": True, "benchmark_result_digest": result.to_mapping()["result_digest"]})

    def derive_export(self, *, export_format: str, export_digest: str, merge_config: Mapping[str, Any] | None = None) -> "ArtifactLineage":
        """Merge/quantization exports are separate, only after evaluation."""
        if export_format not in EXPORT_FORMATS or not _DIGEST.fullmatch(export_digest):
            raise SpecialistCandidateError("specialist_export_invalid")
        if not self.evaluated:
            raise SpecialistCandidateError("specialist_export_requires_evaluation")
        export = {
            "format": export_format,
            "export_digest": export_digest,
            "adapter_digest": self.adapter_digest,
            "base_model_digest": self.base_model_digest,
            "merge_config": dict(merge_config or {}),
            "verified": False,  # a separate evaluation of the export flips this
        }
        return ArtifactLineage(**{**self.__dict__, "exports": (*self.exports, export)})

    def unverified_exports(self) -> list[str]:
        return [str(item["format"]) for item in self.exports if not item.get("verified")]


def lineage_complete(lineage: ArtifactLineage) -> bool:
    return lineage.evaluated and lineage.benchmark_result_digest is not None


def trace_artifact(lineages: Iterable[ArtifactLineage], *, adapter_digest: str) -> ArtifactLineage:
    for lineage in lineages:
        if lineage.adapter_digest == adapter_digest:
            return lineage
    raise SpecialistCandidateError("specialist_lineage_not_found")
