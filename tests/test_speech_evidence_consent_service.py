from __future__ import annotations

import copy

import pytest

from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import SpeechEvidenceGovernanceError
from tests.speech_evidence_support import consent_payload, principal


class Audit:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._recorder = SemanticMediaAuditRecorder(
            SemanticMediaAuditService(
                InMemorySemanticMediaAuditRepository(),
                clock_ms=lambda: 1_000_000,
            ),
            secret=b"speech-consent-audit-test-key" * 2,
        )

    def prepare_transition(self, **kwargs):
        self.calls.append(kwargs)
        return self._recorder.prepare_transition(**kwargs)


def test_grant_reduce_revoke_are_monotone_and_stale_claims_fail() -> None:
    prefix = "consent-state-machine"
    service = SpeechEvidenceConsentService()
    granted = service.grant(principal(prefix), consent_payload(prefix))
    reduced_raw = copy.deepcopy(granted.to_dict())
    reduced_raw["grants"]["raw_audio_share"] = False
    reduced = service.reduce(principal(prefix), reduced_raw, expected_version=1)
    assert service.reduce(principal(prefix), reduced_raw, expected_version=1) == reduced
    revoked = service.revoke(
        principal(prefix), reduced.consent_id, expected_version=2, contributor_id=f"speaker-{prefix}"
    )
    assert (
        service.revoke(
            principal(prefix),
            reduced.consent_id,
            expected_version=2,
            contributor_id=f"recipient-{prefix}",
        )
        == revoked
    )

    assert (granted.consent_version, reduced.consent_version, revoked.consent_version) == (1, 2, 3)
    assert (granted.revocation_epoch, reduced.revocation_epoch, revoked.revocation_epoch) == (0, 1, 2)
    with pytest.raises(SpeechEvidenceGovernanceError) as error:
        service.authorize_claim(
            principal(prefix),
            granted.consent_id,
            expected_consent_version=1,
            expected_revocation_epoch=0,
            expected_consent_digest=granted.consent_digest,
            grant="capture",
            speaker_id=f"speaker-{prefix}",
            recipient_id=f"recipient-{prefix}",
            direction="sender_to_receiver",
            pair_id=f"pair-{prefix}",
            session_id=f"session-{prefix}",
            session_epoch=1,
            purpose="speech_quality_improvement",
            data_class="audio",
        )
    assert error.value.reason_code == "speech_consent_stale_claim"


def test_bilateral_signer_can_read_but_not_mutate_owner_consent() -> None:
    prefix = "consent-participant-read"
    service = SpeechEvidenceConsentService()
    current = service.grant(principal(prefix), consent_payload(prefix))
    recipient = VoicePrincipal(current.tenant_id, current.recipient_id)

    assert service.get(recipient, current.consent_id) == current
    with pytest.raises(SpeechEvidenceGovernanceError) as foreign_read:
        service.get(VoicePrincipal(current.tenant_id, "participant-foreign"), current.consent_id)
    assert foreign_read.value.reason_code == "speech_consent_not_found"
    with pytest.raises(SpeechEvidenceGovernanceError) as foreign_mutation:
        service.revoke(recipient, current.consent_id, expected_version=current.consent_version)
    assert foreign_mutation.value.reason_code == "speech_consent_not_found"


def test_renew_cannot_expand_scope_and_non_hub_cannot_mutate() -> None:
    prefix = "consent-attenuation"
    service = SpeechEvidenceConsentService()
    current = service.grant(principal(prefix), consent_payload(prefix))
    expanded = copy.deepcopy(current.to_dict())
    expanded["grants"]["training"] = True
    expanded["trainer_locations"] = ["trainer-local"]
    with pytest.raises(SpeechEvidenceGovernanceError) as error:
        service.renew(principal(prefix), expanded, expected_version=1)
    assert error.value.reason_code == "speech_consent_scope_expansion_requires_new_grant"

    with pytest.raises(SpeechEvidenceGovernanceError) as error:
        service.revoke(principal(prefix), current.consent_id, expected_version=1, authority="browser")
    assert error.value.reason_code == "speech_consent_hub_authority_required"


def test_consent_mutations_emit_idempotent_content_free_audit_commands() -> None:
    prefix = "consent-audit"
    audit = Audit()
    service = SpeechEvidenceConsentService(audit=audit)
    payload = consent_payload(prefix)
    current = service.grant(principal(prefix), payload)
    replay = service.grant(principal(prefix), payload)
    revoked = service.revoke(
        principal(prefix),
        current.consent_id,
        expected_version=1,
        contributor_id=f"speaker-{prefix}",
    )
    service.revoke(
        principal(prefix),
        current.consent_id,
        expected_version=1,
        contributor_id=f"recipient-{prefix}",
    )

    assert replay == current
    assert revoked.consent_version == 2
    assert [call["transition"] for call in audit.calls] == ["granted", "revoked"]
    assert len({call["idempotency_key"] for call in audit.calls}) == 2
    assert not any(
        forbidden in str(audit.calls).casefold() for forbidden in ("audio_bytes", "transcript", "encryption_key")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speaker_id", "speaker-foreign"),
        ("recipient_id", "recipient-foreign"),
        ("direction", "receiver_to_sender"),
        ("pair_id", "pair-foreign"),
        ("session_id", "session-foreign"),
        ("session_epoch", 2),
        ("purpose", "speech_adaptation_training"),
    ],
)
def test_claim_scope_matrix_rejects_cross_participant_direction_session_and_purpose(
    field: str,
    value: object,
) -> None:
    prefix = f"consent-scope-{field}"
    service = SpeechEvidenceConsentService()
    consent = service.grant(principal(prefix), consent_payload(prefix))
    claim = {
        "speaker_id": consent.speaker_id,
        "recipient_id": consent.recipient_id,
        "direction": consent.direction,
        "pair_id": consent.pair_id,
        "session_id": consent.session_id,
        "session_epoch": consent.session_epoch,
        "purpose": consent.purpose,
    }
    claim[field] = value

    with pytest.raises(SpeechEvidenceGovernanceError) as captured:
        service.authorize_claim(
            principal(prefix),
            consent.consent_id,
            expected_consent_version=consent.consent_version,
            expected_revocation_epoch=consent.revocation_epoch,
            expected_consent_digest=consent.consent_digest,
            grant="transcript_share",
            data_class="transcript",
            **claim,
        )

    assert captured.value.reason_code == "speech_consent_scope_mismatch"


@pytest.mark.parametrize(
    ("principal_override", "version_delta", "epoch_delta", "digest"),
    [
        (("tenant-foreign", "owner-consent-binding"), 0, 0, None),
        (("tenant-consent-binding", "owner-foreign"), 0, 0, None),
        (None, 1, 0, None),
        (None, 0, 1, None),
        (None, 0, 0, "f" * 64),
    ],
)
def test_tenant_owner_and_manipulated_claim_bindings_fail_closed(
    principal_override: tuple[str, str] | None,
    version_delta: int,
    epoch_delta: int,
    digest: str | None,
) -> None:
    from agent.services.voice_governance_domain import VoicePrincipal

    prefix = "consent-binding"
    service = SpeechEvidenceConsentService()
    consent = service.grant(principal(prefix), consent_payload(prefix))
    scoped_principal = VoicePrincipal(*principal_override) if principal_override else principal(prefix)

    with pytest.raises(SpeechEvidenceGovernanceError) as captured:
        service.authorize_claim(
            scoped_principal,
            consent.consent_id,
            expected_consent_version=consent.consent_version + version_delta,
            expected_revocation_epoch=consent.revocation_epoch + epoch_delta,
            expected_consent_digest=digest or consent.consent_digest,
            grant="transcript_share",
            speaker_id=consent.speaker_id,
            recipient_id=consent.recipient_id,
            direction=consent.direction,
            pair_id=consent.pair_id,
            session_id=consent.session_id,
            session_epoch=consent.session_epoch,
            purpose=consent.purpose,
            data_class="transcript",
        )

    assert captured.value.reason_code in {"speech_consent_not_found", "speech_consent_stale_claim"}


def test_replayed_claim_and_ai_snake_authority_are_rejected_after_revocation() -> None:
    prefix = "consent-replay-ai-snake"
    service = SpeechEvidenceConsentService()
    consent = service.grant(principal(prefix), consent_payload(prefix))
    service.revoke(
        principal(prefix),
        consent.consent_id,
        expected_version=consent.consent_version,
        contributor_id=consent.speaker_id,
    )

    with pytest.raises(SpeechEvidenceGovernanceError) as replay:
        service.authorize_claim(
            principal(prefix),
            consent.consent_id,
            expected_consent_version=consent.consent_version,
            expected_revocation_epoch=consent.revocation_epoch,
            expected_consent_digest=consent.consent_digest,
            grant="transcript_share",
            speaker_id=consent.speaker_id,
            recipient_id=consent.recipient_id,
            direction=consent.direction,
            pair_id=consent.pair_id,
            session_id=consent.session_id,
            session_epoch=consent.session_epoch,
            purpose=consent.purpose,
            data_class="transcript",
        )
    assert replay.value.reason_code == "speech_consent_stale_claim"

    fresh_prefix = "consent-ai-snake-mutation"
    with pytest.raises(SpeechEvidenceGovernanceError) as mutation:
        service.grant(
            principal(fresh_prefix),
            consent_payload(fresh_prefix),
            authority="ai-snake",
        )
    assert mutation.value.reason_code == "speech_consent_hub_authority_required"
