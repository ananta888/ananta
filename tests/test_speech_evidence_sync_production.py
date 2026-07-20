from __future__ import annotations

import base64
import hashlib
import threading
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask
from sqlmodel import Session

from agent.bootstrap.semantic_media_services import initialize_semantic_media_services
from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.db_models.speech_evidence_sync import (
    SpeechEvidenceOfferDB,
    SpeechEvidenceTransferChunkDB,
)
from agent.repositories.speech_evidence_sync import (
    SpeechEvidencePeerKeyRecord,
    SpeechEvidenceSyncRepositoryError,
    SpeechEvidenceTransferRecord,
    SqlSpeechEvidenceOfferRepository,
    SqlSpeechEvidencePeerKeyRegistry,
    SqlSpeechEvidenceReplayWindow,
    SqlSpeechEvidenceTransferRepository,
)
from agent.services.speech_evidence_offer_service import (
    HubEvidenceConsent,
    HubPeerAuthorization,
    SpeechEvidenceGroupPreview,
    SpeechEvidenceOfferError,
    SpeechEvidenceOfferRecord,
    SpeechEvidenceOfferService,
    group_preview_digest,
    speech_evidence_speaker_scope_digest,
)
from agent.services.speech_evidence_sync_composition import (
    HubSpeechEvidenceSyncError,
    HubSpeechEvidenceSyncService,
    SqlSpeechEvidenceConsentAdapter,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_sync import (
    OFFER_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SpeechEvidenceMessageVerifier,
    SpeechEvidenceProtocolError,
    SpeechEvidenceReplayWindow,
    canonical_sha256,
    sign_message,
)
from tests.speech_evidence_sync_support import (
    NOW_MS,
    StaticEvidenceKeys,
    comparison_preview,
    digest,
    message,
    payload,
)


def _key_b64(seed: bytes = b"\x11" * 32) -> str:
    key = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    return base64.b64encode(key).decode("ascii")


def _offer(*, groups=("group-a", "group-b"), total_bytes=128) -> SpeechEvidenceOfferRecord:
    previews = tuple(
        SpeechEvidenceGroupPreview.from_mapping({
            "preview_version": "ananta.speech-evidence-group-preview.v1",
            "group_id": group_id,
            "source_group_digest": digest(f"source-{group_id}"),
            "speaker_scope_digest": digest("speaker-scope"),
            "quality_basis": "policy",
            "quality_digest": digest("quality"),
            "resolution_digest": digest(f"resolution-{group_id}"),
            **comparison_preview(digest(f"source-{group_id}"), 1, group_id),
            "revision": 1,
            "size_bytes": 4 if index == 0 else max(1, total_bytes - 4),
        })
        for index, group_id in enumerate(groups)
    )
    return SpeechEvidenceOfferRecord(
        offer_id="offer-test",
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
        group_ids=groups,
        group_previews=previews,
        group_preview_digest=group_preview_digest(previews),
        total_bytes=total_bytes,
        sender_consent_digest=digest("sender-consent"),
        recipient_consent_digest=digest("recipient-consent"),
        scope_digest=digest("scope"),
        expires_at_ms=NOW_MS + 120_000,
        state="accepted",
        transfer_started=True,
        tenant_id="tenant-test",
        protocol_version=OFFER_PROTOCOL_VERSION,
    )


def _verified(raw, *, audience: str):
    return SpeechEvidenceMessageVerifier(
        StaticEvidenceKeys(),
        SpeechEvidenceReplayWindow(),
        clock_ms=lambda: NOW_MS,
    ).verify(
        raw,
        expected_session_id="session-test",
        expected_pair_id="pair-test",
        expected_audience_id=audience,
        expected_epoch=7,
        expected_consent_version=3,
    )


def test_peer_key_registry_is_restart_stable_and_rejects_key_substitution() -> None:
    first_hub = SqlSpeechEvidencePeerKeyRegistry(clock_ms=lambda: NOW_MS)
    values = {
        "tenant_id": "tenant-test",
        "session_id": "session-test",
        "pair_id": "pair-test",
        "sender_id": "peer-a",
        "audience_id": "peer-b",
        "epoch": 7,
        "key_id": "speech-key-test",
        "public_key_b64": _key_b64(),
        "membership_version": 4,
        "consent_version": 3,
        "expires_at_ms": NOW_MS + 60_000,
    }
    created, was_created = first_hub.register(**values)
    replay, replay_created = first_hub.register(**values)
    restarted_hub = SqlSpeechEvidencePeerKeyRegistry(clock_ms=lambda: NOW_MS)

    assert was_created is True and replay_created is False
    assert (
        restarted_hub.get(
            **{
                key: values[key]
                for key in values
                if key not in {"public_key_b64", "membership_version", "consent_version", "expires_at_ms"}
            }
        )
        == created
        == replay
    )

    renewed, renewed_created = restarted_hub.register(
        **{
            **values,
            "consent_version": 4,
            "expires_at_ms": NOW_MS + 90_000,
        }
    )
    assert renewed_created is False
    assert renewed.consent_version == 4 and renewed.version == 2

    with pytest.raises(SpeechEvidenceSyncRepositoryError, match="speech_evidence_key_substitution"):
        restarted_hub.register(**{**values, "public_key_b64": _key_b64(b"\x12" * 32)})


def test_replay_window_is_shared_across_hub_instances_and_epochs_are_fenced() -> None:
    first_hub = SqlSpeechEvidenceReplayWindow(clock_ms=lambda: NOW_MS)
    second_hub = SqlSpeechEvidenceReplayWindow(clock_ms=lambda: NOW_MS)
    key = ("session-test", "pair-test", "peer-a", 7, "control")

    assert first_hub.check(key, 10) is None
    first_hub.commit(key, 10)
    assert second_hub.check(key, 10) == "speech_evidence_replayed"
    with pytest.raises(SpeechEvidenceProtocolError, match="speech_evidence_replayed"):
        second_hub.commit(key, 10)

    second_hub.advance_epoch(session_id="session-test", pair_id="pair-test", minimum_epoch=8)
    assert first_hub.check(key, 10) is None


def test_offer_repository_uses_durable_versioned_compare_and_set() -> None:
    first_hub = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    second_hub = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    preview_payload = payload("offer")["group_previews"]
    previews = tuple(
        SpeechEvidenceGroupPreview.from_mapping(value)
        for value in preview_payload
    )
    proposed = replace(
        _offer(groups=tuple(row.group_id for row in previews)),
        state="proposed",
        transfer_started=False,
        group_previews=previews,
        group_preview_digest=group_preview_digest(previews),
        protocol_version=OFFER_PROTOCOL_VERSION,
    )
    stored = first_hub.put_if_absent(proposed)
    accepted = second_hub.compare_and_set(
        stored.offer_id,
        expected_state="proposed",
        record=replace(stored, state="accepted"),
    )

    assert accepted.state == "accepted" and accepted.version == 2
    assert accepted.group_previews == previews
    assert accepted.group_preview_digest == group_preview_digest(previews)
    assert first_hub.get(stored.offer_id) == accepted
    with pytest.raises(SpeechEvidenceOfferError, match="speech_evidence_offer_state_conflict"):
        first_hub.compare_and_set(
            stored.offer_id,
            expected_state="proposed",
            record=replace(stored, state="invalidated"),
        )


def test_offer_repository_recovery_is_tenant_participant_pair_and_epoch_scoped() -> None:
    repository = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    repository.put_if_absent(_offer())
    repository.put_if_absent(replace(_offer(), offer_id="offer-next-epoch", epoch=8))

    visible = repository.list_for_participant(
        tenant_id="tenant-test",
        session_id="session-test",
        pair_id="pair-test",
        participant_id="peer-a",
        epoch=7,
    )

    assert tuple(record.offer_id for record in visible) == ("offer-test",)
    assert (
        repository.list_for_participant(
            tenant_id="tenant-other",
            session_id="session-test",
            pair_id="pair-test",
            participant_id="peer-a",
            epoch=7,
        )
        == ()
    )
    assert (
        repository.list_for_participant(
            tenant_id="tenant-test",
            session_id="session-test",
            pair_id="pair-test",
            participant_id="peer-foreign",
            epoch=7,
        )
        == ()
    )


def test_curation_guard_serializes_offer_invalidation_on_canonical_row() -> None:
    repository = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    offer = repository.put_if_absent(_offer())
    guard_entered = threading.Event()
    release_guard = threading.Event()
    invalidation_started = threading.Event()
    invalidation_finished = threading.Event()
    failures: list[BaseException] = []

    def curate() -> None:
        try:
            with repository.curation_guard(tenant_id="tenant-test", offer=offer):
                guard_entered.set()
                if not release_guard.wait(timeout=5):
                    raise AssertionError("curation guard race did not resume")
        except BaseException as exc:  # noqa: BLE001 - thread transports the exact failure
            failures.append(exc)

    def invalidate() -> None:
        invalidation_started.set()
        try:
            repository.compare_and_set(
                offer.offer_id,
                expected_state="accepted",
                record=replace(offer, state="invalidated", invalidation_reason="speech_test"),
            )
        except BaseException as exc:  # noqa: BLE001 - thread transports the exact failure
            failures.append(exc)
        finally:
            invalidation_finished.set()

    curation_thread = threading.Thread(target=curate, daemon=True)
    invalidation_thread = threading.Thread(target=invalidate, daemon=True)
    curation_thread.start()
    assert guard_entered.wait(timeout=5)
    invalidation_thread.start()
    assert invalidation_started.wait(timeout=5)
    assert not invalidation_finished.wait(timeout=0.1)
    release_guard.set()
    curation_thread.join(timeout=5)
    invalidation_thread.join(timeout=5)

    assert not curation_thread.is_alive() and not invalidation_thread.is_alive()
    assert failures == []
    assert repository.get(offer.offer_id).state == "invalidated"  # type: ignore[union-attr]


def test_transfer_chunk_ack_and_resume_are_persistent_and_content_free() -> None:
    offers = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    offer = offers.put_if_absent(_offer())
    first_hub = SqlSpeechEvidenceTransferRepository(clock_ms=lambda: NOW_MS)
    second_hub = SqlSpeechEvidenceTransferRepository(clock_ms=lambda: NOW_MS)
    chunk = _verified(message("chunk"), audience="peer-b")

    active = first_hub.register_chunk(tenant_id="tenant-test", offer=offer, message=chunk)
    exact_retry = second_hub.register_chunk(
        tenant_id="tenant-test",
        offer=offer,
        message=_verified(message("chunk", sequence=2), audience="peer-b"),
    )
    with pytest.raises(
        SpeechEvidenceSyncRepositoryError,
        match="speech_evidence_chunk_index_conflict",
    ):
        second_hub.register_chunk(
            tenant_id="tenant-test",
            offer=offer,
            message=_verified(
                message(
                    "chunk",
                    sequence=3,
                    payload_override={"nonce_b64": base64.b64encode(b"o" * 12).decode()},
                ),
                audience="peer-b",
            ),
        )
    resumed = second_hub.get(tenant_id="tenant-test", offer_id=offer.offer_id, group_id="group-a")
    acknowledged = second_hub.acknowledge(
        tenant_id="tenant-test",
        offer=offer,
        message=_verified(
            message("chunk_ack", sequence=2, sender_id="peer-b", audience_id="peer-a"),
            audience="peer-a",
        ),
    )

    assert active == exact_retry == resumed
    assert acknowledged.state == "completed"
    assert acknowledged.first_missing_index == 1
    assert acknowledged.received_bytes == 4
    assert "ciphertext" not in SpeechEvidenceTransferChunkDB.__table__.columns
    assert "ciphertext_b64" not in SpeechEvidenceTransferChunkDB.__table__.columns


def test_transfer_enforces_each_signed_preview_size_before_completion() -> None:
    repository = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    base = _offer()
    too_small = (
        replace(base.group_previews[0], size_bytes=3),
        *base.group_previews[1:],
    )
    offer = repository.put_if_absent(
        replace(
            base,
            offer_id="offer-preview-too-small",
            group_previews=too_small,
            group_preview_digest=group_preview_digest(too_small),
        )
    )
    chunk = message("chunk", payload_override={"offer_id": offer.offer_id})
    transfers = SqlSpeechEvidenceTransferRepository(clock_ms=lambda: NOW_MS)
    with pytest.raises(
        SpeechEvidenceSyncRepositoryError,
        match="speech_evidence_offer_preview_size_exceeded",
    ):
        transfers.register_chunk(
            tenant_id="tenant-test",
            offer=offer,
            message=_verified(chunk, audience="peer-b"),
        )

    expected_five = (
        replace(base.group_previews[0], size_bytes=5),
        *base.group_previews[1:],
    )
    offer = repository.put_if_absent(
        replace(
            base,
            offer_id="offer-preview-incomplete",
            group_previews=expected_five,
            group_preview_digest=group_preview_digest(expected_five),
        )
    )
    transfers.register_chunk(
        tenant_id="tenant-test",
        offer=offer,
        message=_verified(
            message("chunk", payload_override={"offer_id": offer.offer_id}),
            audience="peer-b",
        ),
    )
    with pytest.raises(
        SpeechEvidenceSyncRepositoryError,
        match="speech_evidence_offer_preview_size_mismatch",
    ):
        transfers.acknowledge(
            tenant_id="tenant-test",
            offer=offer,
            message=_verified(
                message(
                    "chunk_ack",
                    sequence=2,
                    sender_id="peer-b",
                    audience_id="peer-a",
                    payload_override={"offer_id": offer.offer_id},
                ),
                audience="peer-a",
            ),
        )


def test_transfer_and_curation_rebind_the_locked_signed_preview_record() -> None:
    raw_preview = payload("offer")["group_previews"][0]
    preview = replace(SpeechEvidenceGroupPreview.from_mapping(raw_preview), size_bytes=4)
    offer = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS).put_if_absent(
        replace(
            _offer(),
            offer_id="offer-derived-preview",
            group_ids=(preview.group_id,),
            group_previews=(preview,),
            group_preview_digest=group_preview_digest((preview,)),
            total_bytes=4,
        )
    )
    transfers = SqlSpeechEvidenceTransferRepository(clock_ms=lambda: NOW_MS)
    chunk = _verified(
        message(
            "chunk",
            payload_override={"offer_id": offer.offer_id, "group_id": preview.group_id},
        ),
        audience="peer-b",
    )
    forged_preview = replace(preview, size_bytes=8)
    with pytest.raises(
        SpeechEvidenceSyncRepositoryError,
        match="speech_evidence_offer_state_conflict",
    ):
        transfers.register_chunk(
            tenant_id="tenant-test",
            offer=replace(
                offer,
                group_previews=(forged_preview,),
                group_preview_digest=group_preview_digest((forged_preview,)),
                total_bytes=8,
            ),
            message=chunk,
        )

    transfers.register_chunk(tenant_id="tenant-test", offer=offer, message=chunk)
    transfers.acknowledge(
        tenant_id="tenant-test",
        offer=offer,
        message=_verified(
            message(
                "chunk_ack",
                sequence=2,
                sender_id="peer-b",
                audience_id="peer-a",
                payload_override={"offer_id": offer.offer_id, "group_id": preview.group_id},
            ),
            audience="peer-a",
        ),
    )
    binding = transfers.curation_binding(
        tenant_id="tenant-test",
        offer_id=offer.offer_id,
        group_id=preview.group_id,
    )
    assert binding is not None
    assert binding.preview == preview
    assert binding.offer_group_preview_digest == offer.group_preview_digest

    with Session(engine) as session:
        row = session.get(SpeechEvidenceOfferDB, offer.offer_id)
        assert row is not None
        row.group_preview_digest = digest("tampered-preview-set")
        session.add(row)
        session.commit()
    assert transfers.curation_binding(
        tenant_id="tenant-test",
        offer_id=offer.offer_id,
        group_id=preview.group_id,
    ) is None

    with Session(engine) as session:
        row = session.get(SpeechEvidenceOfferDB, offer.offer_id)
        assert row is not None
        row.group_preview_digest = offer.group_preview_digest
        row.state = "invalidated"
        session.add(row)
        session.commit()
    assert transfers.curation_binding(
        tenant_id="tenant-test",
        offer_id=offer.offer_id,
        group_id=preview.group_id,
    ) is None


def test_receiver_to_sender_offer_reverses_chunk_and_ack_authority() -> None:
    offers = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    offer = offers.put_if_absent(replace(_offer(), direction="receiver_to_sender"))
    transfers = SqlSpeechEvidenceTransferRepository(clock_ms=lambda: NOW_MS)
    chunk = _verified(
        message("chunk", sender_id="peer-b", audience_id="peer-a"),
        audience="peer-a",
    )
    active = transfers.register_chunk(tenant_id="tenant-test", offer=offer, message=chunk)
    completed = transfers.acknowledge(
        tenant_id="tenant-test",
        offer=offer,
        message=_verified(
            message("chunk_ack", sequence=2, sender_id="peer-a", audience_id="peer-b"),
            audience="peer-b",
        ),
    )
    assert active.state == "active"
    assert completed.state == "completed"


def test_consent_adapter_projects_only_bilateral_current_epoch_grants() -> None:
    scope = {
        "data_classes": ["transcript", "correction", "acoustic_features", "audio"],
        "retention_seconds": 1800,
        "grants": {
            "transcript_share": True,
            "feature_share": True,
            "raw_audio_share": False,
            "dataset_import": True,
            "training": True,
        },
    }
    with Session(engine) as session:
        session.add(
            SpeechEvidenceConsentDB(
                id="consent-peer-a",
                tenant_id="tenant-test",
                owner_subject="peer-a",
                speaker_id="peer-a",
                recipient_id="peer-b",
                pair_id="session-test",
                session_id="session-test",
                session_epoch=7,
                direction="sender_to_receiver",
                purpose="speech_dataset_curation",
                scope_digest=digest("governed-scope"),
                consent_digest=digest("governed-consent"),
                scope_payload=scope,
                required_signers=["peer-a", "peer-b"],
                signature_digests={"peer-a": digest("sig-a"), "peer-b": digest("sig-b")},
                state="active",
                consent_version=5,
                revocation_epoch=0,
                issued_at_ms=NOW_MS - 1_000,
                expires_at_ms=NOW_MS + 60_000,
            )
        )
        session.commit()
    adapter = SqlSpeechEvidenceConsentAdapter(_Epochs(), clock_ms=lambda: NOW_MS)
    consent = adapter.current_scoped(
        tenant_id="tenant-test",
        session_id="session-test",
        pair_id="session-test",
        peer_id="peer-a",
    )
    assert consent is not None
    assert consent.version == 5
    assert consent.data_classes == frozenset(
        {"transcript", "correction", "text_corrections", "vocabulary", "acoustic_features"}
    )
    assert "raw_audio" not in consent.data_classes
    assert consent.trainer_classes == frozenset({"none", "speech_adaptation"})


class _Membership:
    version = 4

    def current(self, *, session_id, pair_id, peer_id, audience_id):
        if session_id != "session-test" or pair_id != session_id or {peer_id, audience_id} != {"peer-a", "peer-b"}:
            return None
        return HubPeerAuthorization(
            tenant_id="tenant-test",
            session_id=session_id,
            pair_id=pair_id,
            peer_id=peer_id,
            audience_id=audience_id,
            epoch=7,
            membership_version=self.version,
            permissions=frozenset({"peer_evidence_sync"}),
            active=True,
        )


class _Consents:
    version = 3

    def __init__(self) -> None:
        self.digest_suffix = ""

    def current_scoped(self, *, pair_id, peer_id, **_scope):
        label = "sender-consent" if peer_id == "peer-a" else "recipient-consent"
        return HubEvidenceConsent(
            peer_id=peer_id,
            pair_id=pair_id,
            version=self.version,
            digest=digest(f"{label}{self.digest_suffix}"),
            directions=frozenset({"sender_to_receiver"}),
            purposes=frozenset({"speech_dataset_curation"}),
            data_classes=frozenset({"text_corrections", "vocabulary"}),
            fields=frozenset({"transcript", "timing"}),
            trainer_classes=frozenset({"none", "speech_adaptation"}),
            maximum_retention_seconds=3600,
            expires_at_ms=NOW_MS + 120_000,
            active=True,
        )


class _Epochs:
    def __init__(self) -> None:
        self.epoch = 7

    def current_epoch(self, **_scope):
        return self.epoch


class _OpaqueRelay:
    def __init__(self) -> None:
        self.rows = []
        self.control_rows = []

    def append_ciphertext(self, **values):
        self.rows.append(values)
        return {"message_id": values["message"].message_id, "cursor": 1}

    def append_message(self, **values):
        self.control_rows.append(values)
        return {"message_id": values["message"].message_id, "cursor": len(self.control_rows)}

    def acknowledge_bytes(self, *_args):
        return None

    def revoke_ciphertext(self, **_values):
        return 0


_PEER_KEYS = {
    "peer-a": Ed25519PrivateKey.from_private_bytes(b"a" * 32),
    "peer-b": Ed25519PrivateKey.from_private_bytes(b"b" * 32),
}


def _signed(message_type: str, *, sender: str, audience: str, sequence: int, overrides=None):
    body = payload(message_type)
    if message_type == "offer":
        speaker_scope_digest = speech_evidence_speaker_scope_digest(
            pair_id="session-test",
            epoch=7,
            speaker_id="peer-a",
        )
        body["group_previews"] = [
            {
                **value,
                "speaker_scope_digest": speaker_scope_digest,
                "size_bytes": 4 if index == 0 else int(body["total_bytes"]) - 4,
            }
            for index, value in enumerate(body["group_previews"])
        ]
    elif message_type in {"chunk", "chunk_ack"}:
        body["group_id"] = str(payload("offer")["group_ids"][0])
    if overrides:
        body.update(overrides)
    unsigned = {
        "protocol_version": OFFER_PROTOCOL_VERSION if message_type == "offer" else PROTOCOL_VERSION,
        "message_type": message_type,
        "message_id": f"production-{sender}-{message_type}-{sequence}",
        "session_id": "session-test",
        "pair_id": "session-test",
        "sender_id": sender,
        "audience_id": audience,
        "epoch": 7,
        "sequence": sequence,
        "consent_version": 3,
        "key_id": f"key-{sender}",
        "issued_at_ms": NOW_MS - 1_000,
        "expires_at_ms": NOW_MS + 60_000,
        "payload_digest": canonical_sha256(body),
        "payload": body,
        "signature_algorithm": "Ed25519",
    }
    return sign_message(unsigned, _PEER_KEYS[sender])


def _relay_envelope(inner):
    ciphertext = b"opaque-pair-encrypted-speech-evidence-envelope"
    return {
        "version": "ananta.webrtc-datachannel.v1",
        "traffic_class": "evidence_bulk" if inner["message_type"] == "chunk" else "control",
        "message_id": f"relay-{inner['message_id']}",
        "session_id": inner["session_id"],
        "epoch": inner["epoch"],
        "sender_id": inner["sender_id"],
        "audience_id": inner["audience_id"],
        "sequence": inner["sequence"],
        "expires_at_ms": inner["expires_at_ms"],
        "compression": "none",
        "security": {"algorithm": "AES-GCM-256", "key_id": "confirmed-pair-key"},
        "payload_bytes": len(ciphertext),
        "payload_digest": hashlib.sha256(ciphertext).hexdigest(),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def test_production_facade_binds_identity_consent_offer_chunk_ack_and_replay() -> None:
    membership = _Membership()
    consents = _Consents()
    epochs = _Epochs()
    keys = SqlSpeechEvidencePeerKeyRegistry(clock_ms=lambda: NOW_MS)
    replay = SqlSpeechEvidenceReplayWindow(clock_ms=lambda: NOW_MS)
    offer_repository = SqlSpeechEvidenceOfferRepository(clock_ms=lambda: NOW_MS)
    offers = SpeechEvidenceOfferService(
        membership=membership,
        consents=consents,
        epochs=epochs,
        repository=offer_repository,
        clock_ms=lambda: NOW_MS,
    )
    transfers = SqlSpeechEvidenceTransferRepository(clock_ms=lambda: NOW_MS)
    relay = _OpaqueRelay()
    service = HubSpeechEvidenceSyncService(
        membership=membership,
        consents=consents,
        epochs=epochs,
        keys=keys,
        replay=replay,
        offer_repository=offer_repository,
        offers=offers,
        transfers=transfers,
        relay=relay,
        control_relay=relay,
        clock_ms=lambda: NOW_MS,
    )
    for sender, audience in (("peer-a", "peer-b"), ("peer-b", "peer-a")):
        raw = (
            _PEER_KEYS[sender]
            .public_key()
            .public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        service.register_key(
            VoicePrincipal("tenant-test", sender),
            session_id="session-test",
            pair_id="session-test",
            audience_id=audience,
            epoch=7,
            consent_version=3,
            key_id=f"key-{sender}",
            public_key_b64=base64.b64encode(raw).decode("ascii"),
            expires_at_ms=NOW_MS + 60_000,
        )

    local_consent, remote_consent = service.current_consent_pair(
        VoicePrincipal("tenant-test", "peer-a"),
        session_id="session-test",
        pair_id="session-test",
        remote_peer_id="peer-b",
        epoch=7,
    )
    assert local_consent.digest == digest("sender-consent")
    assert remote_consent.digest == digest("recipient-consent")
    with pytest.raises(HubSpeechEvidenceSyncError, match="speech_evidence_scope_not_found"):
        service.current_consent_pair(
            VoicePrincipal("tenant-test", "peer-a"),
            session_id="session-test",
            pair_id="session-test",
            remote_peer_id="foreign-peer",
            epoch=7,
        )

    proposal_message = _signed("offer", sender="peer-a", audience="peer-b", sequence=1)
    proposed = service.propose(
        VoicePrincipal("tenant-test", "peer-a"),
        proposal_message,
        _relay_envelope(proposal_message),
    )
    acceptance_message = _signed(
        "offer",
        sender="peer-b",
        audience="peer-a",
        sequence=1,
        overrides={"stage": "acceptance"},
    )
    accepted = service.accept(
        VoicePrincipal("tenant-test", "peer-b"),
        acceptance_message,
        _relay_envelope(acceptance_message),
    )
    assert proposed.state == "proposed" and accepted.state == "accepted"
    assert service.list_offers(
        VoicePrincipal("tenant-test", "peer-a"),
        session_id="session-test",
        pair_id="session-test",
        epoch=7,
    ) == (accepted,)
    assert (
        service.list_offers(
            VoicePrincipal("tenant-test", "foreign-peer"),
            session_id="session-test",
            pair_id="session-test",
            epoch=7,
        )
        == ()
    )
    with pytest.raises(HubSpeechEvidenceSyncError, match="speech_evidence_scope_not_found"):
        service.list_offers(
            VoicePrincipal("tenant-test", "peer-a"),
            session_id="session-test",
            pair_id="pair-other",
            epoch=7,
        )

    epochs.epoch = 8
    assert (
        service.list_offers(
            VoicePrincipal("tenant-test", "peer-a"),
            session_id="session-test",
            pair_id="session-test",
            epoch=7,
        )
        == ()
    )
    epochs.epoch = 7
    consents.digest_suffix = "-renewed"
    assert (
        service.list_offers(
            VoicePrincipal("tenant-test", "peer-a"),
            session_id="session-test",
            pair_id="session-test",
            epoch=7,
        )
        == ()
    )
    consents.digest_suffix = ""

    chunk = _signed("chunk", sender="peer-a", audience="peer-b", sequence=2)
    active, relay_result = service.append_chunk(
        VoicePrincipal("tenant-test", "peer-a"),
        chunk,
        _relay_envelope(chunk),
    )
    ack_message = _signed("chunk_ack", sender="peer-b", audience="peer-a", sequence=2)
    completed = service.acknowledge_chunk(
        VoicePrincipal("tenant-test", "peer-b"),
        ack_message,
        _relay_envelope(ack_message),
    )
    assert active.state == "active" and relay_result["cursor"] == 1
    assert completed.state == "completed" and len(relay.rows) == 1
    assert len(relay.control_rows) == 3
    assert relay.rows[0]["message"].ciphertext == b"opaque-pair-encrypted-speech-evidence-envelope"

    with pytest.raises(SpeechEvidenceProtocolError, match="speech_evidence_replayed"):
        service.append_chunk(
            VoicePrincipal("tenant-test", "peer-a"),
            chunk,
            _relay_envelope(chunk),
        )
    with pytest.raises(HubSpeechEvidenceSyncError, match="speech_evidence_offer_not_found"):
        service.authorize_transfer(VoicePrincipal("tenant-test", "foreign-peer"), accepted.offer_id)

    consents.version = 4
    with pytest.raises(HubSpeechEvidenceSyncError, match="speech_evidence_consent_stale"):
        service.propose(
            VoicePrincipal("tenant-test", "peer-a"),
            _signed("offer", sender="peer-a", audience="peer-b", sequence=3),
        )


class _RouteService(HubSpeechEvidenceSyncService):
    def __init__(self) -> None:
        self.record = None

    def register_key(self, _principal, **values):
        self.record = SpeechEvidencePeerKeyRecord(
            tenant_id="tenant-test",
            session_id=values["session_id"],
            pair_id=values["pair_id"],
            sender_id="admin",
            audience_id=values["audience_id"],
            epoch=values["epoch"],
            key_id=values["key_id"],
            public_key_b64=values["public_key_b64"],
            fingerprint=digest("key"),
            membership_version=1,
            consent_version=values["consent_version"],
            expires_at_ms=values["expires_at_ms"],
            state="active",
            version=1,
        )
        return self.record, True

    def discover_key(self, _principal, **_values):
        return self.record

    def current_consent_pair(self, _principal, **_scope):
        return (
            _Consents().current_scoped(pair_id="session-test", peer_id="peer-a"),
            _Consents().current_scoped(pair_id="session-test", peer_id="peer-b"),
        )

    def propose(self, _principal, _message, _relay_envelope=None):
        return _offer()

    def accept(self, _principal, _message, _relay_envelope=None):
        return replace(_offer(), version=2)

    def list_offers(self, _principal, **_scope):
        return (_offer(),)

    def authorize_transfer(self, _principal, _offer_id):
        return _offer()

    def append_chunk(self, _principal, _message, _relay_envelope):
        return _transfer(), {"message_id": "message-chunk", "cursor": 8, "ciphertext": "hidden"}

    def acknowledge_chunk(self, _principal, _message, _relay_envelope=None):
        return replace(_transfer(), state="completed", first_missing_index=1, acknowledged_chunks=1)

    def transfer_status(self, _principal, **_scope):
        return _transfer()

    def authorize_offer_access(self, _principal, _offer_id):
        return _offer()

    def invalidate(self, _principal, **_scope):
        return replace(_offer(), state="invalidated", invalidation_reason="consent_revoked")


def _transfer() -> SpeechEvidenceTransferRecord:
    return SpeechEvidenceTransferRecord(
        offer_id="offer-test",
        group_id="group-a",
        state="active",
        chunk_count=1,
        acknowledged_chunks=0,
        first_missing_index=0,
        received_bytes=0,
        in_flight_bytes=4,
        expires_at_ms=NOW_MS + 60_000,
        reason_code=None,
        version=1,
    )


def test_key_registration_route_is_additive_closed_and_feature_gated(
    app,
    client,
    admin_auth_header,
) -> None:
    body = {
        "session_id": "session-test",
        "pair_id": "session-test",
        "audience_id": "peer-b",
        "epoch": 7,
        "consent_version": 3,
        "key_id": "speech-key-test",
        "public_key_b64": _key_b64(),
        "expires_at_ms": NOW_MS + 60_000,
    }
    app.extensions["semantic_media_feature_flags"] = {"peer_evidence_sync": False}
    disabled = client.post(
        "/v1/voice/speech-evidence-sync/keys",
        headers=admin_auth_header,
        json=body,
    )
    assert disabled.status_code == 403
    assert disabled.get_json()["error"]["code"] == "semantic_feature_disabled"

    app.extensions["semantic_media_feature_flags"] = {"peer_evidence_sync": True}
    app.extensions["speech_evidence_sync_service"] = _RouteService()
    created = client.post(
        "/v1/voice/speech-evidence-sync/keys",
        headers=admin_auth_header,
        json=body,
    )
    assert created.status_code == 201
    assert created.get_json()["data"]["key"]["public_key_b64"] == body["public_key_b64"]
    assert "tenant_id" not in created.get_json()["data"]["key"]

    unknown = client.post(
        "/v1/voice/speech-evidence-sync/keys",
        headers=admin_auth_header,
        json={**body, "plaintext": "must-never-be-accepted"},
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["error"]["code"] == "speech_evidence_unknown_field"

    discovered = client.get(
        "/v1/voice/speech-evidence-sync/keys/speech-key-test"
        "?session_id=session-test&pair_id=session-test&sender_id=admin&epoch=7",
        headers=admin_auth_header,
    )
    assert discovered.status_code == 200
    assert discovered.get_json()["data"]["key"]["key_id"] == "speech-key-test"


def test_offer_transfer_ack_resume_and_invalidation_route_shapes(
    app,
    client,
    admin_auth_header,
) -> None:
    app.extensions["semantic_media_feature_flags"] = {"peer_evidence_sync": True}
    app.extensions["speech_evidence_sync_service"] = _RouteService()
    signed = {"message": {"signed": "opaque-to-route"}}
    chunk_body = {
        **signed,
        "relay_envelope": {"pair_encrypted": "opaque-to-route"},
    }

    proposal = client.post(
        "/v1/voice/speech-evidence-sync/offers/proposals",
        headers=admin_auth_header,
        json=signed,
    )
    consents = client.get(
        "/v1/voice/speech-evidence-sync/consents/current"
        "?session_id=session-test&pair_id=session-test&remote_peer_id=peer-b&epoch=7",
        headers=admin_auth_header,
    )
    acceptance = client.post(
        "/v1/voice/speech-evidence-sync/offers/acceptances",
        headers=admin_auth_header,
        json=signed,
    )
    listed = client.get(
        "/v1/voice/speech-evidence-sync/offers?session_id=session-test&pair_id=session-test&epoch=7",
        headers=admin_auth_header,
    )
    authorized = client.post(
        "/v1/voice/speech-evidence-sync/offers/offer-test/authorize-transfer",
        headers=admin_auth_header,
    )
    chunk = client.post(
        "/v1/voice/speech-evidence-sync/transfers/chunks",
        headers=admin_auth_header,
        json=chunk_body,
    )
    ack = client.post(
        "/v1/voice/speech-evidence-sync/transfers/acks",
        headers=admin_auth_header,
        json=signed,
    )
    resumed = client.get(
        "/v1/voice/speech-evidence-sync/offers/offer-test/transfers/group-a",
        headers=admin_auth_header,
    )
    invalidated = client.post(
        "/v1/voice/speech-evidence-sync/offers/offer-test/invalidate",
        headers=admin_auth_header,
        json={"reason_code": "consent_revoked"},
    )

    assert proposal.status_code == 201 and acceptance.status_code == 200
    assert consents.status_code == 200
    assert consents.get_json()["data"]["local"]["peer_id"] == "peer-a"
    assert consents.get_json()["data"]["remote"]["peer_id"] == "peer-b"
    assert "tenant_id" not in consents.get_json()["data"]["remote"]
    assert listed.status_code == 200
    assert listed.get_json()["data"]["offers"][0]["offer_id"] == "offer-test"
    assert "tenant_id" not in listed.get_json()["data"]["offers"][0]
    assert authorized.status_code == 200 and authorized.get_json()["data"]["offer"]["transfer_started"] is True
    assert chunk.status_code == 202 and "ciphertext" not in chunk.get_json()["data"]["relay"]
    assert ack.get_json()["data"]["transfer"]["state"] == "completed"
    assert resumed.get_json()["data"]["transfer"]["first_missing_index"] == 0
    assert invalidated.get_json()["data"]["offer"]["state"] == "invalidated"


def test_bootstrap_wires_production_composition_only_when_dependency_chain_is_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("ANANTA_SEMANTIC_MEDIA_BACKGROUND_OPERATIONS_ENABLED", "true")
    monkeypatch.setenv("ANANTA_PEER_EVIDENCE_SYNC_ENABLED", "true")
    target = Flask("speech-evidence-sync-bootstrap")
    target.secret_key = "test-secret-that-is-at-least-32-bytes"
    initialize_semantic_media_services(target)
    assert target.extensions["speech_evidence_sync_composition_status"] == {
        "ready": True,
        "reason_code": None,
    }
    assert isinstance(target.extensions["speech_evidence_sync_service"], HubSpeechEvidenceSyncService)
