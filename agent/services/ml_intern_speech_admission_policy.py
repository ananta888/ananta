"""Fail-closed combination of recipient validation and poisoning policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent.services.speech_evidence_poisoning_policy import SpeechEvidencePoisoningDecision


@dataclass(frozen=True)
class SpeechEvidencePreAdmissionEvidence:
    schema_valid: bool
    signature_valid: bool
    consent_valid: bool
    speaker_scope_valid: bool
    retention_valid: bool
    resolution_valid: bool
    quality_valid: bool
    source_group_valid: bool
    payload_digest_valid: bool
    validation_digest: str


@dataclass(frozen=True)
class MlInternSpeechAdmissionDecision:
    state: str
    reason_codes: tuple[str, ...]
    decision_digest: str


class MlInternSpeechAdmissionPolicy:
    def decide(
        self,
        evidence: SpeechEvidencePreAdmissionEvidence,
        poisoning: SpeechEvidencePoisoningDecision,
    ) -> MlInternSpeechAdmissionDecision:
        reasons: list[str] = []
        hard_checks = {
            "schema_valid": "speech_evidence_schema_invalid",
            "signature_valid": "speech_evidence_signature_invalid",
            "consent_valid": "speech_evidence_consent_stale",
            "speaker_scope_valid": "speech_evidence_speaker_scope_invalid",
            "retention_valid": "speech_evidence_retention_invalid",
            "source_group_valid": "speech_evidence_source_group_invalid",
            "payload_digest_valid": "speech_evidence_payload_digest_mismatch",
        }
        for field, reason in hard_checks.items():
            if not getattr(evidence, field):
                reasons.append(reason)
        if not evidence.resolution_valid:
            reasons.append("speech_evidence_resolution_unverified")
        if not evidence.quality_valid:
            reasons.append("speech_evidence_quality_insufficient")
        reasons.extend(poisoning.reason_codes)
        unique = tuple(dict.fromkeys(reasons))
        hard_failed = any(not getattr(evidence, field) for field in hard_checks)
        state = (
            "reject"
            if hard_failed or poisoning.state == "reject"
            else "quarantine"
            if unique or poisoning.state == "quarantine"
            else "accept"
        )
        digest = hashlib.sha256(
            json.dumps(
                {
                    "policy_version": "ml-intern-speech-peer-admission.v1",
                    "validation_digest": evidence.validation_digest,
                    "poisoning_digest": poisoning.decision_digest,
                    "state": state,
                    "reason_codes": list(unique),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return MlInternSpeechAdmissionDecision(state, unique, digest)


__all__ = [
    "MlInternSpeechAdmissionDecision",
    "MlInternSpeechAdmissionPolicy",
    "SpeechEvidencePreAdmissionEvidence",
]
