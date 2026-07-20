from __future__ import annotations

import pytest

from agent.services.speech_evidence_offer_service import (
    HubEvidenceConsent,
    HubPeerAuthorization,
    InMemorySpeechEvidenceOfferRepository,
    SpeechEvidenceOfferError,
    SpeechEvidenceOfferService,
)
from ananta_contracts.speech_evidence_sync import (
    SpeechEvidenceMessageVerifier,
    SpeechEvidenceReplayWindow,
    group_preview_group_id,
    group_preview_resolution_digest,
)
from tests.speech_evidence_sync_support import (
    NOW_MS,
    StaticEvidenceKeys,
    comparison_preview,
    digest,
    message,
    payload,
)

_OFFER_PAYLOAD = payload("offer")
_GROUP_A = str(_OFFER_PAYLOAD["group_ids"][0])
_PREVIEW_A = dict(_OFFER_PAYLOAD["group_previews"][0])
_FOREIGN_SOURCE = digest("foreign-source")
_FOREIGN_GROUP = group_preview_group_id(_FOREIGN_SOURCE, 1)


class _Membership:
    active = True

    def current(self, *, session_id, pair_id, peer_id, audience_id):
        if not self.active:
            return None
        return HubPeerAuthorization(
            tenant_id="tenant-test",
            session_id=session_id,
            pair_id=pair_id,
            peer_id=peer_id,
            audience_id=audience_id,
            epoch=7,
            membership_version=2,
            permissions=frozenset({"peer_evidence_sync"}),
            active=True,
        )


class _Consents:
    def __init__(self) -> None:
        self.rows = {
            "peer-a": self._row("peer-a", "sender-consent"),
            "peer-b": self._row("peer-b", "recipient-consent"),
        }

    @staticmethod
    def _row(peer_id, label):
        return HubEvidenceConsent(
            peer_id=peer_id,
            pair_id="pair-test",
            version=3,
            digest=digest(label),
            directions=frozenset({"sender_to_receiver"}),
            purposes=frozenset({"speech_dataset_curation"}),
            data_classes=frozenset({"text_corrections", "vocabulary"}),
            fields=frozenset({"transcript", "timing"}),
            trainer_classes=frozenset({"none", "speech_adaptation"}),
            maximum_retention_seconds=3600,
            expires_at_ms=NOW_MS + 120_000,
            active=True,
        )

    def current(self, *, pair_id, peer_id):
        row = self.rows.get(peer_id)
        return row if row and row.pair_id == pair_id else None


class _Epochs:
    epoch = 7

    def current_epoch(self, **_scope):
        return self.epoch


def _verified(raw, *, audience):
    return SpeechEvidenceMessageVerifier(
        StaticEvidenceKeys(), SpeechEvidenceReplayWindow(), clock_ms=lambda: NOW_MS
    ).verify(
        raw,
        expected_session_id="session-test",
        expected_pair_id="pair-test",
        expected_audience_id=audience,
        expected_epoch=7,
        expected_consent_version=3,
    )


def _service():
    membership = _Membership()
    consents = _Consents()
    epochs = _Epochs()
    service = SpeechEvidenceOfferService(
        membership=membership,
        consents=consents,
        epochs=epochs,
        repository=InMemorySpeechEvidenceOfferRepository(),
        clock_ms=lambda: NOW_MS,
    )
    return service, membership, consents, epochs


def _accepted(service, **overrides):
    proposal = _verified(message("offer"), audience="peer-b")
    service.propose(proposal)
    acceptance = message(
        "offer",
        sequence=2,
        sender_id="peer-b",
        audience_id="peer-a",
        payload_override={
            "stage": "acceptance",
            "data_classes": ["text_corrections"],
            "fields": ["transcript"],
            "group_ids": [_GROUP_A],
            "group_previews": [_PREVIEW_A],
            "retention_seconds": 1800,
            "total_bytes": 64,
            **overrides,
        },
    )
    return service.accept(_verified(acceptance, audience="peer-a"))


def test_bilateral_offer_requires_both_signatures_and_recipient_may_reduce_scope() -> None:
    service, *_ = _service()
    accepted = _accepted(service)
    assert accepted.state == "accepted"
    assert accepted.data_classes == ("text_corrections",)
    assert accepted.fields == ("transcript",)
    assert accepted.retention_seconds == 1800
    assert service.authorize_transfer(accepted.offer_id).transfer_started is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"data_classes": ["text_corrections", "vocabulary", "raw_audio"]},
        {"fields": ["transcript", "timing", "private_path"]},
        {
            "group_ids": [_GROUP_A, _FOREIGN_GROUP],
            "group_previews": [
                _PREVIEW_A,
                {
                    **_PREVIEW_A,
                    "group_id": _FOREIGN_GROUP,
                    "source_group_digest": _FOREIGN_SOURCE,
                    "resolution_digest": group_preview_resolution_digest(_FOREIGN_SOURCE, 1),
                    **comparison_preview(_FOREIGN_SOURCE, 1, "foreign-group"),
                    "size_bytes": 1,
                },
            ],
            "total_bytes": 65,
        },
        {"retention_seconds": 3601},
        {"total_bytes": 129, "group_previews": [{**_PREVIEW_A, "size_bytes": 129}]},
        {"scope_digest": digest("foreign-scope")},
    ],
)
def test_recipient_cannot_expand_scope(overrides) -> None:
    service, *_ = _service()
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        _accepted(service, **overrides)
    assert captured.value.reason_code in {
        "speech_evidence_offer_scope_denied",
        "speech_evidence_offer_scope_expansion",
    }


def test_partial_consent_epoch_change_permission_loss_and_revoke_invalidate_offer() -> None:
    service, membership, consents, epochs = _service()
    accepted = _accepted(service)
    consents.rows["peer-b"] = HubEvidenceConsent(
        **{**consents.rows["peer-b"].__dict__, "digest": digest("changed-consent")}
    )
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        service.authorize_transfer(accepted.offer_id)
    assert captured.value.reason_code == "speech_evidence_offer_consent_stale"

    service, membership, _consents, epochs = _service()
    accepted = _accepted(service)
    epochs.epoch = 8
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        service.authorize_transfer(accepted.offer_id)
    assert captured.value.reason_code == "speech_evidence_epoch_stale"

    service, membership, _consents, _epochs = _service()
    accepted = _accepted(service)
    membership.active = False
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        service.authorize_transfer(accepted.offer_id)
    assert captured.value.reason_code == "speech_evidence_membership_denied"


def test_offer_calls_are_idempotent_but_conflicting_replay_is_rejected() -> None:
    service, *_ = _service()
    proposal = _verified(message("offer"), audience="peer-b")
    assert service.propose(proposal) == service.propose(proposal)
    conflicting_previews = [dict(value) for value in _OFFER_PAYLOAD["group_previews"]]
    conflicting_previews[-1]["size_bytes"] = 63
    conflicting = _verified(
        message(
            "offer",
            sequence=2,
            payload_override={"total_bytes": 127, "group_previews": conflicting_previews},
        ),
        audience="peer-b",
    )
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        service.propose(conflicting)
    assert captured.value.reason_code == "speech_evidence_offer_id_conflict"


def test_preview_scope_is_hub_checked_and_acceptance_cannot_rewrite_a_group_binding() -> None:
    service, *_ = _service()
    invalid_quality = [dict(value) for value in _OFFER_PAYLOAD["group_previews"]]
    invalid_quality[0]["quality_digest"] = digest("sender-chosen-quality")
    proposal = _verified(
        message("offer", payload_override={"group_previews": invalid_quality}),
        audience="peer-b",
    )
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        service.propose(proposal)
    assert captured.value.reason_code == "speech_evidence_offer_preview_scope_denied"

    rewritten = {**_PREVIEW_A, "speaker_scope_digest": digest("expanded-speaker-scope")}
    with pytest.raises(SpeechEvidenceOfferError) as captured:
        _accepted(service, group_previews=[rewritten])
    assert captured.value.reason_code == "speech_evidence_offer_scope_expansion"
