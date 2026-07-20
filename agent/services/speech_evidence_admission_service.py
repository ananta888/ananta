"""Hub-side admission of recipient-owned, encrypted peer evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.ml_intern_speech_admission_policy import (
    MlInternSpeechAdmissionDecision,
    MlInternSpeechAdmissionPolicy,
    SpeechEvidencePreAdmissionEvidence,
)
from agent.services.speech_evidence_offer_service import (
    SpeechEvidenceOfferRecord,
    speech_evidence_quality_policy_digest,
)
from agent.services.speech_evidence_poisoning_policy import (
    EvidenceCandidateRiskSignal,
    SpeechEvidencePoisoningPolicy,
)


class SpeechEvidenceAdmissionError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class CurrentSpeechEvidenceOfferPort(Protocol):
    def authorize_transfer(self, offer_id: str) -> SpeechEvidenceOfferRecord: ...


class RecipientEvidenceDecisionPort(Protocol):
    def transition(
        self,
        record_id: str,
        *,
        target: str,
        expected_state: str,
        decision_digest: str,
        reason_codes: tuple[str, ...],
    ): ...


class SpeechEvidenceAdmissionCapacityPort(Protocol):
    def reserve(self, *, pair_id: str, record_id: str, size_bytes: int) -> bool: ...

    def release(self, *, pair_id: str, record_id: str) -> None: ...


@dataclass(frozen=True)
class SpeechEvidenceAdmissionCommand:
    record_id: str
    offer_id: str
    pair_id: str
    sender_id: str
    group_id: str
    consent_digest: str
    resolution_digest: str
    source_group_digest: str
    speaker_digest: str
    size_bytes: int
    pre_admission: SpeechEvidencePreAdmissionEvidence
    risk_signals: tuple[EvidenceCandidateRiskSignal, ...]


class SpeechEvidenceAdmissionService:
    def __init__(
        self,
        *,
        offers: CurrentSpeechEvidenceOfferPort,
        recipient: RecipientEvidenceDecisionPort,
        capacity: SpeechEvidenceAdmissionCapacityPort,
        poisoning: SpeechEvidencePoisoningPolicy | None = None,
        policy: MlInternSpeechAdmissionPolicy | None = None,
    ) -> None:
        self._offers = offers
        self._recipient = recipient
        self._capacity = capacity
        self._poisoning = poisoning or SpeechEvidencePoisoningPolicy()
        self._policy = policy or MlInternSpeechAdmissionPolicy()

    def admit(self, command: SpeechEvidenceAdmissionCommand) -> MlInternSpeechAdmissionDecision:
        offer = self._offers.authorize_transfer(command.offer_id)
        preview = next(
            (value for value in offer.group_previews if value.group_id == command.group_id),
            None,
        )
        if (
            offer.pair_id != command.pair_id
            or offer.sender_id != command.sender_id
            or preview is None
            or offer.sender_consent_digest != command.consent_digest
            or preview.source_group_digest != command.source_group_digest
            or preview.speaker_scope_digest != command.speaker_digest
            or preview.resolution_digest != command.resolution_digest
            or preview.size_bytes != command.size_bytes
            or preview.quality_basis != "policy"
            or preview.quality_digest != speech_evidence_quality_policy_digest()
        ):
            raise SpeechEvidenceAdmissionError("speech_evidence_admission_offer_mismatch", status_code=403)
        if command.size_bytes <= 0:
            raise SpeechEvidenceAdmissionError("speech_evidence_admission_size_invalid")
        if not self._capacity.reserve(
            pair_id=command.pair_id,
            record_id=command.record_id,
            size_bytes=command.size_bytes,
        ):
            decision = MlInternSpeechAdmissionDecision(
                state="quarantine",
                reason_codes=("speech_evidence_admission_quota_exceeded",),
                decision_digest=command.pre_admission.validation_digest,
            )
            self._transition(command, decision)
            return decision
        try:
            poisoning = self._poisoning.evaluate(command.risk_signals)
            decision = self._policy.decide(command.pre_admission, poisoning)
            self._transition(command, decision)
            return decision
        finally:
            if "decision" not in locals() or decision.state != "accept":
                self._capacity.release(pair_id=command.pair_id, record_id=command.record_id)

    def _transition(
        self,
        command: SpeechEvidenceAdmissionCommand,
        decision: MlInternSpeechAdmissionDecision,
    ) -> None:
        self._recipient.transition(
            command.record_id,
            target={"accept": "accepted", "reject": "rejected", "quarantine": "quarantined"}[decision.state],
            expected_state="quarantined",
            decision_digest=decision.decision_digest,
            reason_codes=decision.reason_codes,
        )


__all__ = [
    "CurrentSpeechEvidenceOfferPort",
    "RecipientEvidenceDecisionPort",
    "SpeechEvidenceAdmissionCapacityPort",
    "SpeechEvidenceAdmissionCommand",
    "SpeechEvidenceAdmissionError",
    "SpeechEvidenceAdmissionService",
]
