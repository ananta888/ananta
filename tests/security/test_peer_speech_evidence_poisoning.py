from __future__ import annotations

import pytest

from agent.services.speech_evidence_poisoning_policy import (
    EvidenceCandidateRiskSignal,
    SpeechEvidencePoisoningPolicy,
)
from tests.speech_evidence_sync_support import digest


def _signal(index: int = 0, **changes) -> EvidenceCandidateRiskSignal:
    values = {
        "candidate_id": f"candidate-{index}",
        "source_role": "speaker",
        "contributor_digest": digest(f"contributor-{index}"),
        "lineage_digest": digest(f"lineage-{index}"),
        "model_digest": digest(f"model-{index}"),
        "validator_set_digest": digest(f"validators-{index}"),
        "signature_valid": True,
        "digest_valid": True,
        "speaker_scope_valid": True,
        "replayed": False,
        "confidence_micros": 900_000,
        "contradictory_revision_count": 0,
        "distribution_distance_micros": 10_000,
        "trigger_phrase_detected": False,
    }
    values.update(changes)
    return EvidenceCandidateRiskSignal(**values)


@pytest.mark.parametrize(
    ("signal", "state", "reason"),
    [
        (_signal(speaker_scope_valid=False), "reject", "speech_evidence_malicious_speaker_scope"),
        (_signal(source_role="listener", signature_valid=False), "reject", "speech_evidence_provenance_invalid"),
        (_signal(source_role="asr", digest_valid=False), "reject", "speech_evidence_provenance_invalid"),
        (
            _signal(source_role="model", distribution_distance_micros=900_000),
            "quarantine",
            "speech_evidence_distribution_anomaly",
        ),
        (_signal(replayed=True), "reject", "speech_evidence_replay_detected"),
        (_signal(trigger_phrase_detected=True), "quarantine", "speech_evidence_targeted_trigger_detected"),
        (_signal(contradictory_revision_count=4), "quarantine", "speech_evidence_false_correction_series"),
        (
            _signal(confidence_micros=1_000_000, distribution_distance_micros=300_000),
            "quarantine",
            "speech_evidence_confidence_unrealistic",
        ),
    ],
)
def test_malicious_roles_replay_false_correction_and_trigger_phrase_fail_closed(signal, state, reason) -> None:
    decision = SpeechEvidencePoisoningPolicy().evaluate((signal,))
    assert decision.state == state
    assert reason in decision.reason_codes


def test_correlated_models_and_revisions_count_as_one_lineage_not_false_majority() -> None:
    shared = {
        "lineage_digest": digest("shared-lineage"),
        "model_digest": digest("shared-model"),
        "validator_set_digest": digest("shared-validators"),
    }
    decision = SpeechEvidencePoisoningPolicy().evaluate(
        (_signal(1, **shared), _signal(2, **shared), _signal(3, **shared))
    )
    assert decision.independent_lineage_count == 1
    assert len(decision.collapsed_candidate_ids) == 2
    assert "speech_evidence_correlated_votes_collapsed" in decision.reason_codes


def test_colluding_validator_pattern_is_quarantined_and_evidence_is_content_free() -> None:
    contributor = digest("colluding-validator")
    rows = tuple(
        _signal(index, contributor_digest=contributor)
        for index in range(4)
    )
    decision = SpeechEvidencePoisoningPolicy().evaluate(rows)
    assert decision.state == "quarantine"
    assert "speech_evidence_validator_collusion_suspected" in decision.reason_codes
    public = repr(decision)
    assert "trigger phrase contents" not in public
    assert all(" " not in reason for reason in decision.reason_codes)
