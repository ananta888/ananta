"""Hub-side decision policy for immutable speech adaptation reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.speech_adaptation_evaluation import SpeechEvaluationError, validate_evaluation_report


@dataclass(frozen=True)
class SpeechEvaluationDecision:
    report_digest: str
    passed: bool
    approval_eligible: bool
    reason_codes: tuple[str, ...]
    policy_version: str


class MlInternSpeechEvalService:
    """Evaluate content-free worker reports without trusting worker decisions."""

    def __init__(
        self,
        *,
        allowed_policy_versions: tuple[str, ...] = ("speech-eval-policy.v1",),
        minimum_validation_samples: int = 1,
    ) -> None:
        if not allowed_policy_versions or not 1 <= minimum_validation_samples <= 1_000_000:
            raise ValueError("speech evaluation policy configuration is invalid")
        self._versions = frozenset(allowed_policy_versions)
        self._minimum_samples = minimum_validation_samples

    def decide(
        self,
        report: Mapping[str, Any],
        *,
        expected_bindings: Mapping[str, str],
    ) -> SpeechEvaluationDecision:
        digest = validate_evaluation_report(report)
        bindings = report.get("bindings")
        if not isinstance(bindings, Mapping) or dict(bindings) != dict(expected_bindings):
            raise SpeechEvaluationError(
                "speech_evaluation_binding_mismatch",
                "evaluation report does not match the Hub admission bindings",
            )
        reasons: list[str] = []
        policy = str(report.get("policy_version") or "")
        if policy not in self._versions:
            reasons.append("speech_evaluation_policy_not_admitted")
        counts = report.get("sample_counts") if isinstance(report.get("sample_counts"), Mapping) else {}
        if any(int(value) < self._minimum_samples for value in counts.values()):
            reasons.append("speech_evaluation_sample_count_insufficient")
        metrics = report.get("metrics") if isinstance(report.get("metrics"), Mapping) else {}
        probes = report.get("probes") if isinstance(report.get("probes"), Mapping) else {}
        if any(not bool(value.get("passed")) for value in metrics.values() if isinstance(value, Mapping)):
            reasons.append("speech_evaluation_metric_regression")
        if any(not bool(value.get("passed")) for value in probes.values() if isinstance(value, Mapping)):
            reasons.append("speech_evaluation_safety_probe_failed")
        if not bool(report.get("passed")):
            reasons.append("speech_evaluation_worker_decision_failed")
        hardware = str(report.get("hardware_profile") or "")
        lifecycle_mock = hardware == "mock-cpu-no-model"
        if lifecycle_mock:
            reasons.append("speech_evaluation_mock_has_no_quality_claim")
        passed = not any(
            reason
            for reason in reasons
            if reason != "speech_evaluation_mock_has_no_quality_claim"
        )
        return SpeechEvaluationDecision(
            report_digest=digest,
            passed=passed,
            approval_eligible=passed and not lifecycle_mock,
            reason_codes=tuple(dict.fromkeys(reasons)),
            policy_version=policy,
        )
