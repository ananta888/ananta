from __future__ import annotations

import pytest

from agent.services.speech_evidence_encryption_port import AesGcmSpeechEvidenceEncryption
from agent.services.speech_evidence_key_service import SpeechEvidenceKeyService
from ananta_contracts.speech_evidence_crypto import SpeechEvidenceCryptoError


def test_separate_deks_tamper_rotation_and_destruction() -> None:
    keys = SpeechEvidenceKeyService(master_key=b"k" * 32)
    crypto = AesGcmSpeechEvidenceEncryption(keys)
    first = crypto.encrypt(
        b"private transcript",
        artifact_ref="speech-evidence-key-test-a",
        artifact_class="evidence",
        tenant_id="tenant-key-test",
        pair_id="pair-key-test",
        purpose="curation",
        session_epoch=1,
        key_epoch=1,
    )
    second = crypto.encrypt(
        b"private transcript",
        artifact_ref="speech-evidence-key-test-b",
        artifact_class="checkpoint",
        tenant_id="tenant-key-test",
        pair_id="pair-key-test",
        purpose="training",
        session_epoch=1,
        key_epoch=1,
    )
    assert first.key_id != second.key_id and first.content_digest != second.content_digest
    assert crypto.decrypt(first) == b"private transcript"

    keys.rotate(
        first.key_id,
        tenant_id=first.tenant_id,
        pair_id=first.pair_id,
        purpose=first.purpose,
        artifact_class=first.artifact_class,
        artifact_ref=first.artifact_ref,
        current_key_epoch=1,
        next_key_epoch=2,
    )
    assert crypto.decrypt(first) == b"private transcript"
    assert crypto.destroy(first.key_id, tenant_id=first.tenant_id)
    with pytest.raises(SpeechEvidenceCryptoError) as error:
        crypto.decrypt(first)
    assert error.value.reason_code == "speech_crypto_key_destroyed"


def test_strict_e2ee_blocks_server_crypto() -> None:
    crypto = AesGcmSpeechEvidenceEncryption(SpeechEvidenceKeyService(master_key=b"s" * 32))
    with pytest.raises(SpeechEvidenceCryptoError) as error:
        crypto.encrypt(
            b"x",
            artifact_ref="speech-evidence-strict-e2ee",
            artifact_class="evidence",
            tenant_id="tenant-strict",
            pair_id="pair-strict",
            purpose="capture",
            session_epoch=1,
            key_epoch=1,
            security_mode="strict_e2ee",
        )
    assert error.value.reason_code == "speech_crypto_strict_e2ee_server_forbidden"
