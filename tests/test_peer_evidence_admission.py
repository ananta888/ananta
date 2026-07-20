from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent.services.ml_intern_speech_admission_policy import SpeechEvidencePreAdmissionEvidence
from agent.services.speech_evidence_admission_service import (
    SpeechEvidenceAdmissionCommand,
    SpeechEvidenceAdmissionError,
    SpeechEvidenceAdmissionService,
)
from agent.services.speech_evidence_offer_service import (
    SpeechEvidenceGroupPreview,
    SpeechEvidenceOfferRecord,
    group_preview_digest,
    speech_evidence_quality_policy_digest,
    speech_evidence_speaker_scope_digest,
)
from agent.services.speech_evidence_poisoning_policy import EvidenceCandidateRiskSignal
from ananta_contracts.speech_evidence_sync import (
    GROUP_PREVIEW_VERSION,
    OFFER_PROTOCOL_VERSION,
    group_preview_group_id,
    group_preview_resolution_digest,
)
from tests.speech_evidence_sync_support import comparison_preview, digest
from voice_runtime.peer_evidence_admission import (
    InMemoryRecipientQuarantineRepository,
    LocalEvidenceValidation,
    PeerEvidenceAdmissionError,
    RecipientPeerEvidenceQuarantine,
)

NOW = 1_000_000
SOURCE_GROUP_DIGEST = digest("source-group")
GROUP_REVISION = 1
GROUP_ID = group_preview_group_id(SOURCE_GROUP_DIGEST, GROUP_REVISION)
SPEAKER_SCOPE_DIGEST = speech_evidence_speaker_scope_digest(
    pair_id="pair-test",
    epoch=7,
    speaker_id="peer-a",
)
RESOLUTION_DIGEST = group_preview_resolution_digest(SOURCE_GROUP_DIGEST, GROUP_REVISION)


class _Keys:
    value = b"k" * 32

    def __init__(self) -> None:
        self.destroyed = []

    def resolve(self, **_scope):
        return self.value

    def destroy(self, **scope):
        self.destroyed.append(scope)


class _Validator:
    def __init__(self, *, valid=True) -> None:
        self.valid = valid

    def validate(self, plaintext, **_scope):
        content = bytes(plaintext)
        return LocalEvidenceValidation(
            schema_valid=self.valid,
            signature_valid=self.valid,
            consent_valid=self.valid,
            speaker_scope_valid=self.valid,
            resolution_valid=self.valid,
            quality_valid=self.valid,
            source_group_valid=self.valid,
            content_digest=hashlib.sha256(content).hexdigest(),
            feature_digest=digest("features"),
            reason_codes=() if self.valid else ("speech_evidence_schema_invalid",),
        )


def _quarantine(*, maximum_records=10, validator=None):
    keys = _Keys()
    service = RecipientPeerEvidenceQuarantine(
        keys=keys,
        validator=validator or _Validator(),
        repository=InMemoryRecipientQuarantineRepository(),
        maximum_records=maximum_records,
        clock_ms=lambda: NOW,
    )
    return service, keys


def _store(service, *, clear=b"encrypted peer evidence", group_id="group-a", retention=2_000_000):
    nonce = b"n" * 12
    aad = b"bound-aad"
    ciphertext = AESGCM(_Keys.value).encrypt(nonce, clear, aad)
    return service.quarantine_encrypted(
        pair_id="pair-test",
        offer_id="offer-test",
        group_id=group_id,
        sender_id="peer-a",
        speaker_digest=digest("speaker"),
        source_group_digest=digest("source-group"),
        consent_digest=digest("sender-consent"),
        resolution_digest=digest("resolution"),
        payload_digest=hashlib.sha256(clear).hexdigest(),
        ciphertext=ciphertext,
        nonce=nonce,
        aad=aad,
        key_id="key-test",
        epoch=7,
        retention_until_ms=retention,
    )


def _risk(**changes):
    values = {
        "candidate_id": "candidate-a",
        "source_role": "speaker",
        "contributor_digest": digest("contributor"),
        "lineage_digest": digest("lineage"),
        "model_digest": digest("model"),
        "validator_set_digest": digest("validators"),
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


def test_received_payload_remains_encrypted_and_digest_idempotent_until_hub_decision() -> None:
    service, keys = _quarantine()
    record = _store(service)
    replay = _store(service)
    assert replay.record_id == record.record_id
    public = service.public(record.record_id)
    assert "ciphertext_b64" not in public and "aad_b64" not in public
    assert b"encrypted peer evidence" not in record.ciphertext_b64.encode()
    checked, validation = service.pre_admit(record.record_id)
    assert validation.schema_valid and checked.state == "quarantined"
    accepted = service.transition(
        record.record_id,
        target="accepted",
        decision_digest=digest("decision"),
        reason_codes=(),
    )
    assert accepted.state == "accepted"
    service.delete(record.record_id, expected_state="accepted")
    assert keys.destroyed


def test_digest_mismatch_stale_retention_and_quota_race_fail_closed() -> None:
    service, _keys = _quarantine(maximum_records=1)
    record = _store(service)
    with pytest.raises(PeerEvidenceAdmissionError) as captured:
        _store(service, clear=b"different", group_id="group-b")
    assert captured.value.reason_code == "speech_evidence_quarantine_quota_exceeded"

    stale, _keys = _quarantine()
    with pytest.raises(PeerEvidenceAdmissionError) as captured:
        _store(stale, retention=NOW)
    assert captured.value.reason_code == "speech_evidence_quarantine_scope_stale"

    record = record.__class__(**{**record.__dict__, "payload_digest": digest("wrong")})
    repository = InMemoryRecipientQuarantineRepository()
    repository.put_if_absent(record)
    broken = RecipientPeerEvidenceQuarantine(
        keys=_Keys(), validator=_Validator(), repository=repository, clock_ms=lambda: NOW
    )
    with pytest.raises(PeerEvidenceAdmissionError) as captured:
        broken.pre_admit(record.record_id)
    assert captured.value.reason_code == "speech_evidence_quarantine_digest_mismatch"


class _Offer:
    def authorize_transfer(self, offer_id):
        assert offer_id == "offer-test"
        preview = SpeechEvidenceGroupPreview.from_mapping({
            "preview_version": GROUP_PREVIEW_VERSION,
            "group_id": GROUP_ID,
            "source_group_digest": SOURCE_GROUP_DIGEST,
            "speaker_scope_digest": SPEAKER_SCOPE_DIGEST,
            "quality_basis": "policy",
            "quality_digest": speech_evidence_quality_policy_digest(),
            "resolution_digest": RESOLUTION_DIGEST,
            **comparison_preview(SOURCE_GROUP_DIGEST, GROUP_REVISION, "admission"),
            "revision": GROUP_REVISION,
            "size_bytes": 128,
        })
        return SpeechEvidenceOfferRecord(
            offer_id=offer_id,
            proposal_verification_digest=digest("proposal"),
            acceptance_verification_digest=digest("acceptance"),
            session_id="session-test",
            pair_id="pair-test",
            epoch=7,
            sender_id="peer-a",
            recipient_id="peer-b",
            inventory_root_digest=digest("inventory"),
            direction="sender_to_receiver",
            purpose="speech_dataset_curation",
            data_classes=("text_corrections",),
            fields=("transcript",),
            retention_seconds=3600,
            trainer_class="speech_adaptation",
            group_ids=(GROUP_ID,),
            group_previews=(preview,),
            group_preview_digest=group_preview_digest((preview,)),
            total_bytes=128,
            sender_consent_digest=digest("sender-consent"),
            recipient_consent_digest=digest("recipient-consent"),
            scope_digest=digest("scope"),
            expires_at_ms=2_000_000,
            state="accepted",
            transfer_started=True,
            protocol_version=OFFER_PROTOCOL_VERSION,
        )


class _Recipient:
    def __init__(self):
        self.calls = []

    def transition(self, record_id, **values):
        self.calls.append((record_id, values))


class _Capacity:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.released = []

    def reserve(self, **_values):
        return self.allowed

    def release(self, **values):
        self.released.append(values)


def _command(**changes):
    pre = SpeechEvidencePreAdmissionEvidence(
        schema_valid=True,
        signature_valid=True,
        consent_valid=True,
        speaker_scope_valid=True,
        retention_valid=True,
        resolution_valid=True,
        quality_valid=True,
        source_group_valid=True,
        payload_digest_valid=True,
        validation_digest=digest("validation"),
    )
    values = {
        "record_id": "record-test",
        "offer_id": "offer-test",
        "pair_id": "pair-test",
        "sender_id": "peer-a",
        "group_id": GROUP_ID,
        "consent_digest": digest("sender-consent"),
        "resolution_digest": RESOLUTION_DIGEST,
        "source_group_digest": SOURCE_GROUP_DIGEST,
        "speaker_digest": SPEAKER_SCOPE_DIGEST,
        "size_bytes": 128,
        "pre_admission": pre,
        "risk_signals": (_risk(),),
    }
    values.update(changes)
    return SpeechEvidenceAdmissionCommand(**values)


def test_hub_admission_rechecks_offer_and_never_exposes_quarantine_to_training() -> None:
    recipient = _Recipient()
    service = SpeechEvidenceAdmissionService(
        offers=_Offer(), recipient=recipient, capacity=_Capacity()
    )
    decision = service.admit(_command())
    assert decision.state == "accept"
    assert recipient.calls[0][1]["target"] == "accepted"
    assert not hasattr(service, "train") and not hasattr(service, "build_dataset")

    with pytest.raises(SpeechEvidenceAdmissionError) as captured:
        service.admit(_command(sender_id="malicious-peer"))
    assert captured.value.reason_code == "speech_evidence_admission_offer_mismatch"

    for mismatch in (
        {"resolution_digest": digest("wrong-resolution")},
        {"source_group_digest": digest("wrong-source")},
        {"speaker_digest": digest("wrong-speaker")},
        {"size_bytes": 127},
    ):
        with pytest.raises(SpeechEvidenceAdmissionError) as captured:
            service.admit(_command(**mismatch))
        assert captured.value.reason_code == "speech_evidence_admission_offer_mismatch"


def test_quota_and_stale_or_malicious_validation_remain_quarantined_or_rejected() -> None:
    recipient = _Recipient()
    no_capacity = SpeechEvidenceAdmissionService(
        offers=_Offer(), recipient=recipient, capacity=_Capacity(allowed=False)
    )
    assert no_capacity.admit(_command()).state == "quarantine"
    assert recipient.calls[-1][1]["target"] == "quarantined"

    invalid = SpeechEvidencePreAdmissionEvidence(
        **{**_command().pre_admission.__dict__, "consent_valid": False}
    )
    recipient = _Recipient()
    service = SpeechEvidenceAdmissionService(
        offers=_Offer(), recipient=recipient, capacity=_Capacity()
    )
    assert service.admit(_command(pre_admission=invalid)).state == "reject"
    assert recipient.calls[-1][1]["target"] == "rejected"
