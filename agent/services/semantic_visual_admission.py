"""Hub-owned, fail-closed admission boundary for semantic visual activation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VisualReleaseEvidence:
    passed: bool
    verified: bool
    policy_version: str
    artifact_digest: str


class VisualReleaseGatePort(Protocol):
    def current(self) -> VisualReleaseEvidence | None: ...


class VisualSfuAdmissionPort(Protocol):
    def ready(self, *, session_id: str, epoch: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class VisualAdmissionRequest:
    session_id: str
    epoch: int
    now_ms: int
    capture_flag: bool
    sfu_flag: bool
    consent_active: bool
    contract_id: str | None
    contract_expires_at_ms: int
    lease_id: str | None
    lease_expires_at_ms: int
    quality_report_id: str | None
    quality_report_expires_at_ms: int


@dataclass(frozen=True, slots=True)
class VisualAdmissionDecision:
    semantic_active: bool
    delivery_mode: str
    reason_code: str
    ordinary_baseline_available: bool = True


class SemanticVisualAdmissionService:
    """Consumes authority; never creates contracts, leases, flags or quality."""

    def __init__(
        self,
        *,
        release_gate: VisualReleaseGatePort | None,
        sfu_admission: VisualSfuAdmissionPort | None,
    ) -> None:
        self._release_gate = release_gate
        self._sfu_admission = sfu_admission

    def admit(self, request: VisualAdmissionRequest) -> VisualAdmissionDecision:
        if not request.session_id or request.epoch < 1 or request.now_ms < 0:
            return _ordinary("visual_admission_invalid")
        if not request.capture_flag or not request.sfu_flag:
            return _ordinary("visual_feature_disabled")
        evidence = self._release_gate.current() if self._release_gate is not None else None
        if (
            evidence is None
            or not evidence.passed
            or not evidence.verified
            or evidence.policy_version != "semantic-visual-release-gate/1.0.0"
            or len(evidence.artifact_digest) != 64
        ):
            return _ordinary("visual_release_gate_closed")
        if not request.consent_active:
            return _ordinary("visual_consent_missing")
        if request.contract_id is None or request.contract_expires_at_ms <= request.now_ms:
            return _ordinary("visual_contract_missing_or_stale")
        if request.lease_id is None or request.lease_expires_at_ms <= request.now_ms:
            return _ordinary("visual_lease_missing_or_stale")
        if request.quality_report_id is None or request.quality_report_expires_at_ms <= request.now_ms:
            return _ordinary("visual_quality_report_missing_or_stale")
        if self._sfu_admission is None or not self._sfu_admission.ready(
            session_id=request.session_id, epoch=request.epoch
        ):
            return _ordinary("visual_sfu_admission_unavailable")
        return VisualAdmissionDecision(True, "semantic", "visual_hub_admitted", True)


def _ordinary(reason_code: str) -> VisualAdmissionDecision:
    return VisualAdmissionDecision(False, "ordinary", reason_code, True)


__all__ = [
    "SemanticVisualAdmissionService",
    "VisualAdmissionDecision",
    "VisualAdmissionRequest",
    "VisualReleaseEvidence",
    "VisualReleaseGatePort",
    "VisualSfuAdmissionPort",
]
