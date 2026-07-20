from __future__ import annotations

import pytest

from ananta_contracts.speech_evidence_governance import (
    SPEECH_GRANTS,
    SpeechEvidenceConsent,
    SpeechEvidenceGovernanceError,
    migrate_legacy_categories,
)
from tests.speech_evidence_support import consent_payload


def test_all_sensitive_grants_are_independent_and_default_deny() -> None:
    raw = consent_payload("contract-default")
    raw["grants"] = {"capture": True}
    consent = SpeechEvidenceConsent.from_mapping(raw)

    assert consent.grants == {name: name == "capture" for name in SPEECH_GRANTS}


def test_legacy_categories_never_expand_to_dataset_training_audio_or_export() -> None:
    grants = migrate_legacy_categories(["preferences", "text_corrections"])

    assert grants["inference"] is True
    assert not any(grants[name] for name in ("dataset_import", "training", "raw_audio_share", "export"))


def test_unknown_grant_and_incomplete_bilateral_signatures_fail_closed() -> None:
    raw = consent_payload("contract-closed")
    raw["grants"] = {**raw["grants"], "ai_snake_says_yes": True}
    with pytest.raises(SpeechEvidenceGovernanceError, match="unknown speech grants"):
        SpeechEvidenceConsent.from_mapping(raw)

    raw = consent_payload("contract-signature")
    raw["signatures"] = {}
    with pytest.raises(SpeechEvidenceGovernanceError) as error:
        SpeechEvidenceConsent.from_mapping(raw)
    assert error.value.reason_code == "speech_consent_signatures_incomplete"
