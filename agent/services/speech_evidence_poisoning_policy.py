"""Content-free poisoning, collusion and false-correction policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceCandidateRiskSignal:
    candidate_id: str
    source_role: str
    contributor_digest: str
    lineage_digest: str
    model_digest: str
    validator_set_digest: str
    signature_valid: bool
    digest_valid: bool
    speaker_scope_valid: bool
    replayed: bool
    confidence_micros: int
    contradictory_revision_count: int
    distribution_distance_micros: int
    trigger_phrase_detected: bool


@dataclass(frozen=True)
class SpeechEvidencePoisoningDecision:
    state: str
    reason_codes: tuple[str, ...]
    independent_lineage_count: int
    collapsed_candidate_ids: tuple[str, ...]
    decision_digest: str


class SpeechEvidencePoisoningPolicy:
    def __init__(
        self,
        *,
        maximum_contradictory_revisions: int = 3,
        maximum_distribution_distance_micros: int = 750_000,
    ) -> None:
        self._maximum_revisions = maximum_contradictory_revisions
        self._maximum_distance = maximum_distribution_distance_micros

    def evaluate(
        self,
        signals: tuple[EvidenceCandidateRiskSignal, ...],
    ) -> SpeechEvidencePoisoningDecision:
        if not signals or len(signals) > 64:
            raise ValueError("speech_evidence_risk_signal_count_invalid")
        reasons: list[str] = []
        reject = False
        for item in signals:
            self._validate(item)
            if not item.signature_valid or not item.digest_valid:
                reasons.append("speech_evidence_provenance_invalid")
                reject = True
            if item.replayed:
                reasons.append("speech_evidence_replay_detected")
                reject = True
            if not item.speaker_scope_valid:
                reasons.append("speech_evidence_malicious_speaker_scope")
                reject = True
            if item.trigger_phrase_detected:
                reasons.append("speech_evidence_targeted_trigger_detected")
            if item.contradictory_revision_count > self._maximum_revisions:
                reasons.append("speech_evidence_false_correction_series")
            if item.distribution_distance_micros > self._maximum_distance:
                reasons.append("speech_evidence_distribution_anomaly")
            if item.confidence_micros >= 999_500 and (
                item.contradictory_revision_count or item.distribution_distance_micros > 250_000
            ):
                reasons.append("speech_evidence_confidence_unrealistic")

        # A model/revision/validator cluster has one vote regardless of how many
        # candidates or purported validators repeat it.
        groups: dict[tuple[str, str, str], list[str]] = {}
        for item in signals:
            groups.setdefault(
                (item.lineage_digest, item.model_digest, item.validator_set_digest),
                [],
            ).append(item.candidate_id)
        collapsed = tuple(
            sorted(candidate_id for rows in groups.values() for candidate_id in sorted(rows)[1:])
        )
        if collapsed:
            reasons.append("speech_evidence_correlated_votes_collapsed")
        contributor_to_groups: dict[str, set[tuple[str, str, str]]] = {}
        for item in signals:
            contributor_to_groups.setdefault(item.contributor_digest, set()).add(
                (item.lineage_digest, item.model_digest, item.validator_set_digest)
            )
        if any(len(rows) >= 4 for rows in contributor_to_groups.values()):
            reasons.append("speech_evidence_validator_collusion_suspected")

        unique_reasons = tuple(dict.fromkeys(reasons))
        state = "reject" if reject else "quarantine" if unique_reasons else "accept"
        raw = {
            "policy_version": "speech-evidence-poisoning.v1",
            "state": state,
            "reason_codes": list(unique_reasons),
            "independent_lineage_count": len(groups),
            "candidate_ids": sorted(item.candidate_id for item in signals),
            "collapsed_candidate_ids": list(collapsed),
        }
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return SpeechEvidencePoisoningDecision(
            state=state,
            reason_codes=unique_reasons,
            independent_lineage_count=len(groups),
            collapsed_candidate_ids=collapsed,
            decision_digest=digest,
        )

    @staticmethod
    def _validate(item: EvidenceCandidateRiskSignal) -> None:
        if item.source_role not in {"speaker", "listener", "asr", "model", "validator"}:
            raise ValueError("speech_evidence_source_role_invalid")
        if not 0 <= item.confidence_micros <= 1_000_000:
            raise ValueError("speech_evidence_confidence_invalid")
        if not 0 <= item.contradictory_revision_count <= 1_000_000:
            raise ValueError("speech_evidence_revision_signal_invalid")
        if not 0 <= item.distribution_distance_micros <= 10_000_000:
            raise ValueError("speech_evidence_distribution_signal_invalid")
        for digest in (
            item.contributor_digest,
            item.lineage_digest,
            item.model_digest,
            item.validator_set_digest,
        ):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("speech_evidence_risk_digest_invalid")


__all__ = [
    "EvidenceCandidateRiskSignal",
    "SpeechEvidencePoisoningDecision",
    "SpeechEvidencePoisoningPolicy",
]
