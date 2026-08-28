"""Deterministic gate binding an evaluation report to a curated fixture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agent.services.local_adapter_evaluation_service import (
    LocalAdapterEvaluationReport,
)
from ananta_contracts.local_adapter_evaluation_fixture import (
    CuratedLocalAdapterEvaluationFixture,
)


@dataclass(frozen=True, slots=True)
class CuratedEvaluationGateResult:
    passed: bool
    fixture_sha256: str
    slice_results: Mapping[str, bool]
    reason_codes: tuple[str, ...]


class LocalAdapterEvaluationFixtureGate:
    """Apply immutable per-slice thresholds; it never runs a model."""

    def assess(
        self,
        fixture: CuratedLocalAdapterEvaluationFixture,
        report: LocalAdapterEvaluationReport,
    ) -> CuratedEvaluationGateResult:
        fixture_sha256 = fixture.sha256
        if report.golden_set_sha256 != fixture_sha256:
            return CuratedEvaluationGateResult(
                passed=False,
                fixture_sha256=fixture_sha256,
                slice_results={},
                reason_codes=("local_adapter_fixture_digest_mismatch",),
            )
        results = {
            slice_id: (
                float(report.slice_accuracy.get(slice_id, 0.0)) >= float(threshold.minimum_accuracy)
                and float(report.slice_regressions.get(slice_id, 1.0)) <= float(threshold.maximum_regression)
            )
            for slice_id, threshold in sorted(fixture.thresholds.items())
        }
        reasons = tuple(
            f"local_adapter_fixture_slice_failed:{slice_id}" for slice_id, passed in results.items() if not passed
        )
        return CuratedEvaluationGateResult(
            passed=not reasons,
            fixture_sha256=fixture_sha256,
            slice_results=results,
            reason_codes=reasons or ("local_adapter_fixture_gate_passed",),
        )


__all__ = [
    "CuratedEvaluationGateResult",
    "LocalAdapterEvaluationFixtureGate",
]
