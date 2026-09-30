"""Production Hub composition for bilateral peer speech-evidence sync.

The Hub owns identity, membership, consent, epoch and transfer state.  Evidence
chunks remain opaque AES-GCM ciphertext and are delegated only to the existing
bounded semantic relay; this module never accepts a content-encryption key or
plaintext evidence.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from agent.repositories.speech_evidence_sync import (
    SpeechEvidencePeerKeyRecord,
    SpeechEvidenceSyncRepositoryError,
    SpeechEvidenceTransferCurationBinding,
    SpeechEvidenceTransferRecord,
    SqlSpeechEvidenceOfferRepository,
    SqlSpeechEvidencePeerKeyRegistry,
    SqlSpeechEvidenceReplayWindow,
    SqlSpeechEvidenceTransferRepository,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.semantic_relay_composition import get_semantic_relay_service
from agent.services.semantic_relay_service import SemanticRelayService
from agent.services.semantic_speech_relay import SemanticSpeechRelay, SpeechRelayLimits
from agent.services.share_session_service import get_share_session_service
from agent.services.speech_evidence_offer_service import (
    HubEvidenceConsent,
    HubPeerAuthorization,
    SpeechEvidenceOfferError,
    SpeechEvidenceOfferRecord,
    SpeechEvidenceOfferService,
)
from agent.services.speech_evidence_sync_consent_adapter import SqlSpeechEvidenceConsentAdapter
from agent.services.speech_evidence_sync_ports import (
    SpeechEvidenceControlRelayPort,
    SpeechEvidenceOpaqueRelayPort,
)
from agent.services.speech_evidence_sync_share_session_adapters import (
    ShareSessionSpeechEvidenceEpoch,
    ShareSessionSpeechEvidenceMembership,
)
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from ananta_contracts.speech_evidence_sync import (
    SpeechEvidenceMessageVerifier,
    SpeechEvidenceProtocolError,
    VerifiedSpeechEvidenceMessage,
    parse_bounded_message,
    parse_header,
)
from ananta_contracts.webrtc_datachannel import (
    CONTRACT_VERSION,
    DataChannelContractError,
    ValidatedDataChannelMessage,
    validate_message,
)


class HubSpeechEvidenceSyncError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


PEER_CURATION_REQUEST_POLICY_DIGEST = hashlib.sha256(
    b"ananta.peer-speech-hub-curation-request.v1"
).hexdigest()


class _TenantMembershipKeyResolver:
    def __init__(
        self,
        registry: SqlSpeechEvidencePeerKeyRegistry,
        *,
        tenant_id: str,
        membership_version: int,
        consent_version: int,
    ) -> None:
        self._registry = registry
        self._tenant_id = tenant_id
        self._membership_version = membership_version
        self._consent_version = consent_version

    def resolve(self, **scope: object):
        record = self._registry.get(tenant_id=self._tenant_id, **scope)
        if (
            record is None
            or record.membership_version != self._membership_version
            or record.consent_version != self._consent_version
        ):
            return None
        return self._registry.resolve(tenant_id=self._tenant_id, **scope)


class HubSpeechEvidenceSyncService:
    """Authenticated facade over immutable keys, signed offers and opaque transfer state."""

    def __init__(
        self,
        *,
        membership: ShareSessionSpeechEvidenceMembership,
        consents: SqlSpeechEvidenceConsentAdapter,
        epochs: ShareSessionSpeechEvidenceEpoch,
        keys: SqlSpeechEvidencePeerKeyRegistry,
        replay: SqlSpeechEvidenceReplayWindow,
        offer_repository: SqlSpeechEvidenceOfferRepository,
        offers: SpeechEvidenceOfferService,
        transfers: SqlSpeechEvidenceTransferRepository,
        relay: SpeechEvidenceOpaqueRelayPort,
        control_relay: SpeechEvidenceControlRelayPort | None = None,
        clock_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._membership = membership
        self._consents = consents
        self._epochs = epochs
        self._keys = keys
        self._replay = replay
        self._offer_repository = offer_repository
        self._offers = offers
        self._transfers = transfers
        self._relay = relay
        self._control_relay = control_relay
        self._clock_ms = clock_ms

    def register_key(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        pair_id: str,
        audience_id: str,
        epoch: int,
        consent_version: int,
        key_id: str,
        public_key_b64: str,
        expires_at_ms: int,
    ) -> tuple[SpeechEvidencePeerKeyRecord, bool]:
        authority = self._require_authority(
            principal,
            session_id=session_id,
            pair_id=pair_id,
            sender_id=principal.subject,
            audience_id=audience_id,
            epoch=epoch,
        )
        consent = self._require_consent(authority, principal.subject, consent_version)
        if expires_at_ms > consent.expires_at_ms:
            raise HubSpeechEvidenceSyncError("speech_evidence_key_expiry_exceeds_consent", status_code=422)
        return self._keys.register(
            tenant_id=principal.tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            sender_id=principal.subject,
            audience_id=audience_id,
            epoch=epoch,
            key_id=key_id,
            public_key_b64=public_key_b64,
            membership_version=authority.membership_version,
            consent_version=consent.version,
            expires_at_ms=expires_at_ms,
        )

    def discover_key(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        pair_id: str,
        sender_id: str,
        epoch: int,
        key_id: str,
    ) -> SpeechEvidencePeerKeyRecord:
        authority = self._membership.current(
            session_id=session_id,
            pair_id=pair_id,
            peer_id=sender_id,
            audience_id=principal.subject,
        )
        if authority is None or authority.tenant_id != principal.tenant_id:
            raise HubSpeechEvidenceSyncError("speech_evidence_key_not_found", status_code=404)
        if authority.epoch != epoch:
            raise HubSpeechEvidenceSyncError("speech_evidence_epoch_stale", status_code=409)
        record = self._keys.get(
            tenant_id=principal.tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            sender_id=sender_id,
            audience_id=principal.subject,
            epoch=epoch,
            key_id=key_id,
        )
        if record is None or record.membership_version != authority.membership_version:
            raise HubSpeechEvidenceSyncError("speech_evidence_key_not_found", status_code=404)
        consent = self._consents.current_scoped(
            tenant_id=authority.tenant_id,
            session_id=authority.session_id,
            pair_id=authority.pair_id,
            peer_id=sender_id,
        )
        if consent is None or consent.version != record.consent_version:
            raise HubSpeechEvidenceSyncError("speech_evidence_key_not_found", status_code=404)
        return record

    def propose(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any] | bytes,
        relay_envelope: Mapping[str, Any] | None = None,
    ) -> SpeechEvidenceOfferRecord:
        message = self._verify(principal, raw, expected_type="offer")
        result = self._offers.propose(message)
        self._relay_control(principal, message, relay_envelope)
        return result

    def accept(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any] | bytes,
        relay_envelope: Mapping[str, Any] | None = None,
    ) -> SpeechEvidenceOfferRecord:
        message = self._verify(principal, raw, expected_type="offer")
        result = self._offers.accept(message)
        self._relay_control(principal, message, relay_envelope)
        return result

    def authorize_transfer(self, principal: VoicePrincipal, offer_id: str) -> SpeechEvidenceOfferRecord:
        record = self._require_offer_access(principal, offer_id)
        authorized = self._offers.authorize_transfer(record.offer_id)
        if authorized.tenant_id != principal.tenant_id:
            raise HubSpeechEvidenceSyncError("speech_evidence_offer_not_found", status_code=404)
        return authorized

    def current_consent_pair(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        pair_id: str,
        remote_peer_id: str,
        epoch: int,
    ) -> tuple[HubEvidenceConsent, HubEvidenceConsent]:
        """Return content-free current consent authority for one exact pair."""

        authority = self._require_authority(
            principal,
            session_id=session_id,
            pair_id=pair_id,
            sender_id=principal.subject,
            audience_id=remote_peer_id,
            epoch=epoch,
        )
        if self._epochs.current_epoch(session_id=session_id, pair_id=pair_id) != epoch:
            raise HubSpeechEvidenceSyncError("speech_evidence_epoch_stale", status_code=409)
        local = self._consents.current_scoped(
            tenant_id=authority.tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            peer_id=principal.subject,
        )
        remote = self._consents.current_scoped(
            tenant_id=authority.tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            peer_id=remote_peer_id,
        )
        if local is None or remote is None or not local.active or not remote.active:
            raise HubSpeechEvidenceSyncError("speech_evidence_consent_missing", status_code=403)
        # The wire header carries one bilateral consent version, while each
        # participant may have a distinct signed consent digest. Divergent
        # versions cannot be represented safely and therefore fail closed.
        if local.version != remote.version:
            raise HubSpeechEvidenceSyncError("speech_evidence_consent_ambiguous", status_code=409)
        return local, remote

    def list_offers(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        pair_id: str,
        epoch: int,
    ) -> tuple[SpeechEvidenceOfferRecord, ...]:
        """Recover bounded, content-free offer state for the authenticated peer.

        The repository query is tenant- and participant-scoped.  Every result
        is then revalidated through current bilateral membership so a stale or
        revoked participant cannot use this read model as an ID oracle.
        """

        if pair_id != session_id:
            raise HubSpeechEvidenceSyncError("speech_evidence_scope_not_found", status_code=404)
        candidates = self._offer_repository.list_for_participant(
            tenant_id=principal.tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            participant_id=principal.subject,
            epoch=epoch,
            limit=50,
        )
        visible: list[SpeechEvidenceOfferRecord] = []
        for record in candidates:
            try:
                current = self._require_offer_access(principal, record.offer_id)
                current = self._offers.recover_current(current.offer_id)
            except (HubSpeechEvidenceSyncError, SpeechEvidenceOfferError):
                continue
            if current.epoch == epoch and current.session_id == session_id and current.pair_id == pair_id:
                visible.append(current)
        return tuple(visible)

    def append_chunk(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any] | bytes,
        relay_envelope: Mapping[str, Any],
    ) -> tuple[SpeechEvidenceTransferRecord, dict]:
        message = self._verify(principal, raw, expected_type="chunk")
        outer = validate_message(relay_envelope)
        self._require_outer_binding(message, outer)
        offer_id = str(message.payload["offer_id"])
        offer = self.authorize_transfer(principal, offer_id)
        transfer_sender, _transfer_recipient = _transfer_participants(offer)
        if principal.subject != transfer_sender:
            raise HubSpeechEvidenceSyncError("speech_evidence_offer_not_found", status_code=404)
        transfer = self._transfers.register_chunk(
            tenant_id=principal.tenant_id,
            offer=offer,
            message=message,
        )
        relay_result = self._relay.append_ciphertext(
            tenant_id=principal.tenant_id,
            authenticated_sender_id=principal.subject,
            offer_id=offer.offer_id,
            message=outer,
        )
        return transfer, relay_result

    def acknowledge_chunk(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any] | bytes,
        relay_envelope: Mapping[str, Any] | None = None,
    ) -> SpeechEvidenceTransferRecord:
        message = self._verify(principal, raw, expected_type="chunk_ack")
        offer = self.authorize_transfer(principal, str(message.payload["offer_id"]))
        transfer_sender, transfer_recipient = _transfer_participants(offer)
        if principal.subject != transfer_recipient:
            raise HubSpeechEvidenceSyncError("speech_evidence_offer_not_found", status_code=404)
        before = self._transfers.get(
            tenant_id=principal.tenant_id,
            offer_id=offer.offer_id,
            group_id=str(message.payload["group_id"]),
        )
        result = self._transfers.acknowledge(
            tenant_id=principal.tenant_id,
            offer=offer,
            message=message,
        )
        if before is not None:
            newly_acked = max(0, result.acknowledged_chunks - before.acknowledged_chunks)
            released = max(0, before.in_flight_bytes - result.in_flight_bytes) + 16 * newly_acked
            self._relay.acknowledge_bytes(
                offer.offer_id,
                transfer_sender,
                transfer_recipient,
                released,
            )
        self._relay_control(principal, message, relay_envelope)
        return result

    def transfer_status(
        self,
        principal: VoicePrincipal,
        *,
        offer_id: str,
        group_id: str,
    ) -> SpeechEvidenceTransferRecord:
        offer = self._require_offer_access(principal, offer_id)
        if offer.state != "accepted" or not offer.transfer_started:
            raise HubSpeechEvidenceSyncError("speech_evidence_transfer_not_found", status_code=404)
        # Revalidate current consent, membership and epoch without changing an
        # already-started offer.
        offer = self._offers.authorize_transfer(offer.offer_id)
        result = self._transfers.get(
            tenant_id=principal.tenant_id,
            offer_id=offer.offer_id,
            group_id=group_id,
        )
        if result is None:
            raise HubSpeechEvidenceSyncError("speech_evidence_transfer_not_found", status_code=404)
        return result

    def authorize_curation_request(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any] | bytes,
    ) -> tuple[
        VerifiedSpeechEvidenceMessage,
        SpeechEvidenceOfferRecord,
        tuple[SpeechEvidenceTransferCurationBinding, ...],
    ]:
        """Authorize a recipient request to move quarantine into Hub curation.

        The signed wire receipt is deliberately a *quarantine attestation*:
        the browser must put every offered group into ``quarantined`` and may
        not choose accepted/rejected groups.  Admission, receipt issuance and
        dataset materialisation therefore remain Hub-owned.
        """

        message = self._verify(principal, raw, expected_type="receipt")
        offer = self.authorize_transfer(principal, str(message.payload.get("offer_id") or ""))
        _sender, recipient = _transfer_participants(offer)
        payload = message.payload
        groups = tuple(sorted(str(value) for value in payload["quarantined_group_ids"]))
        expected_consent = (
            offer.sender_consent_digest
            if principal.subject == offer.sender_id
            else offer.recipient_consent_digest
        )
        expected_result = hashlib.sha256(
            json.dumps(
                {"accepted": [], "quarantined": list(groups), "rejected": []},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            principal.subject != recipient
            or offer.trainer_class != "speech_adaptation"
            or payload.get("inventory_root_digest") != offer.inventory_root_digest
            or payload.get("consent_digest") != expected_consent
            or payload.get("policy_digest") != PEER_CURATION_REQUEST_POLICY_DIGEST
            or payload.get("result_digest") != expected_result
            or payload.get("accepted_group_ids") != []
            or payload.get("rejected_group_ids") != []
            or groups != tuple(sorted(offer.group_ids))
        ):
            raise HubSpeechEvidenceSyncError("speech_evidence_curation_request_denied", status_code=403)
        bindings: list[SpeechEvidenceTransferCurationBinding] = []
        for group_id in groups:
            binding = self._transfers.curation_binding(
                tenant_id=principal.tenant_id,
                offer_id=offer.offer_id,
                group_id=group_id,
            )
            if binding is None:
                raise HubSpeechEvidenceSyncError(
                    "speech_evidence_curation_transfer_incomplete", status_code=409
                )
            bindings.append(binding)
        return message, offer, tuple(bindings)

    @contextmanager
    def curation_offer_guard(
        self,
        principal: VoicePrincipal,
        offer: SpeechEvidenceOfferRecord,
    ) -> Iterator[None]:
        """Keep the canonical accepted Offer stable through Hub curation."""

        current = self._require_offer_access(principal, offer.offer_id)
        if current != offer:
            raise HubSpeechEvidenceSyncError(
                "speech_evidence_curation_offer_stale",
                status_code=409,
            )
        try:
            with self._offer_repository.curation_guard(
                tenant_id=principal.tenant_id,
                offer=offer,
            ):
                yield
        except SpeechEvidenceSyncRepositoryError as exc:
            raise HubSpeechEvidenceSyncError(exc.reason_code, status_code=exc.status_code) from exc

    def invalidate(
        self,
        principal: VoicePrincipal,
        *,
        offer_id: str,
        reason_code: str,
    ) -> SpeechEvidenceOfferRecord:
        current = self._require_offer_access(principal, offer_id)
        result = self._offers.invalidate(offer_id, reason_code=reason_code)
        self._transfers.invalidate_offer(
            tenant_id=principal.tenant_id,
            offer_id=offer_id,
            reason_code=reason_code,
        )
        self._relay.revoke_ciphertext(
            tenant_id=principal.tenant_id,
            session_id=current.session_id,
            epoch=current.epoch,
            offer_id=offer_id,
            message_ids=self._transfers.message_ids(
                tenant_id=principal.tenant_id,
                offer_id=offer_id,
            ),
        )
        return result

    def authorize_offer_access(
        self,
        principal: VoicePrincipal,
        offer_id: str,
    ) -> SpeechEvidenceOfferRecord:
        """Authorize a participant before a composed Hub-side mutation."""

        return self._require_offer_access(principal, offer_id)

    def _verify(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any] | bytes,
        *,
        expected_type: str,
    ) -> VerifiedSpeechEvidenceMessage:
        mapping = parse_bounded_message(raw)
        header = parse_header(mapping)
        if header.message_type != expected_type:
            raise HubSpeechEvidenceSyncError("speech_evidence_message_type_mismatch", status_code=422)
        authority = self._require_authority(
            principal,
            session_id=header.session_id,
            pair_id=header.pair_id,
            sender_id=header.sender_id,
            audience_id=header.audience_id,
            epoch=header.epoch,
        )
        consent = self._require_consent(authority, header.sender_id, header.consent_version)
        verifier = SpeechEvidenceMessageVerifier(
            _TenantMembershipKeyResolver(
                self._keys,
                tenant_id=principal.tenant_id,
                membership_version=authority.membership_version,
                consent_version=consent.version,
            ),
            self._replay,  # type: ignore[arg-type]
            clock_ms=self._clock_ms,
        )
        return verifier.verify(
            mapping,
            expected_session_id=header.session_id,
            expected_pair_id=header.pair_id,
            expected_audience_id=header.audience_id,
            expected_epoch=authority.epoch,
            expected_consent_version=consent.version,
        )

    @staticmethod
    def _require_outer_binding(
        inner: VerifiedSpeechEvidenceMessage,
        outer: ValidatedDataChannelMessage,
        *,
        expected_traffic: str = "evidence_bulk",
    ) -> None:
        if (
            outer.version != CONTRACT_VERSION
            or outer.traffic_class != expected_traffic
            or outer.session_id != inner.header.session_id
            or outer.epoch != inner.header.epoch
            or outer.sender_id != inner.header.sender_id
            or outer.audience_id != inner.header.audience_id
            or outer.sequence != inner.header.sequence
            or outer.expires_at_ms != inner.header.expires_at_ms
        ):
            raise HubSpeechEvidenceSyncError("speech_evidence_relay_binding_mismatch", status_code=403)

    def _relay_control(
        self,
        principal: VoicePrincipal,
        inner: VerifiedSpeechEvidenceMessage,
        relay_envelope: Mapping[str, Any] | None,
    ) -> None:
        if relay_envelope is None:
            return
        if self._control_relay is None:
            raise HubSpeechEvidenceSyncError("speech_evidence_relay_unavailable", status_code=503)
        outer = validate_message(relay_envelope)
        self._require_outer_binding(inner, outer, expected_traffic="control")
        self._control_relay.append_message(
            tenant_id=principal.tenant_id,
            authenticated_sender_id=principal.subject,
            message=outer,
        )

    def _require_authority(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        pair_id: str,
        sender_id: str,
        audience_id: str,
        epoch: int,
    ) -> HubPeerAuthorization:
        if principal.subject != sender_id:
            raise HubSpeechEvidenceSyncError("speech_evidence_sender_mismatch", status_code=403)
        authority = self._membership.current(
            session_id=session_id,
            pair_id=pair_id,
            peer_id=sender_id,
            audience_id=audience_id,
        )
        if authority is None or authority.tenant_id != principal.tenant_id:
            raise HubSpeechEvidenceSyncError("speech_evidence_scope_not_found", status_code=404)
        if authority.epoch != epoch:
            raise HubSpeechEvidenceSyncError("speech_evidence_epoch_stale", status_code=409)
        return authority

    def _require_consent(
        self,
        authority: HubPeerAuthorization,
        peer_id: str,
        expected_version: int,
    ) -> HubEvidenceConsent:
        consent = self._consents.current_scoped(
            tenant_id=authority.tenant_id,
            session_id=authority.session_id,
            pair_id=authority.pair_id,
            peer_id=peer_id,
        )
        if consent is None:
            raise HubSpeechEvidenceSyncError("speech_evidence_consent_missing", status_code=403)
        if consent.version != expected_version:
            raise HubSpeechEvidenceSyncError("speech_evidence_consent_stale", status_code=409)
        return consent

    def _require_offer_access(
        self,
        principal: VoicePrincipal,
        offer_id: str,
    ) -> SpeechEvidenceOfferRecord:
        record = self._offer_repository.get(offer_id)
        if (
            record is None
            or record.tenant_id != principal.tenant_id
            or principal.subject not in {record.sender_id, record.recipient_id}
        ):
            raise HubSpeechEvidenceSyncError("speech_evidence_offer_not_found", status_code=404)
        other = record.recipient_id if principal.subject == record.sender_id else record.sender_id
        authority = self._membership.current(
            session_id=record.session_id,
            pair_id=record.pair_id,
            peer_id=principal.subject,
            audience_id=other,
        )
        if authority is None or authority.tenant_id != principal.tenant_id:
            raise HubSpeechEvidenceSyncError("speech_evidence_offer_not_found", status_code=404)
        return record


@dataclass(frozen=True)
class SpeechEvidenceSyncComposition:
    service: HubSpeechEvidenceSyncService
    membership: ShareSessionSpeechEvidenceMembership
    consents: SqlSpeechEvidenceConsentAdapter
    epochs: ShareSessionSpeechEvidenceEpoch
    keys: SqlSpeechEvidencePeerKeyRegistry
    replay: SqlSpeechEvidenceReplayWindow
    offers: SqlSpeechEvidenceOfferRepository
    transfers: SqlSpeechEvidenceTransferRepository


def build_speech_evidence_sync_composition(
    *,
    audit: SemanticMediaAuditPort | None = None,
) -> SpeechEvidenceSyncComposition:
    epoch_service = get_webrtc_epoch_service()
    epoch_adapter = ShareSessionSpeechEvidenceEpoch(epoch_service)
    membership = ShareSessionSpeechEvidenceMembership(
        get_share_session_service(),
        epoch_service,
    )
    consents = SqlSpeechEvidenceConsentAdapter(epoch_adapter)
    keys = SqlSpeechEvidencePeerKeyRegistry()
    replay = SqlSpeechEvidenceReplayWindow()
    offer_repository = SqlSpeechEvidenceOfferRepository()
    offer_service = SpeechEvidenceOfferService(
        membership=membership,
        consents=consents,
        epochs=epoch_adapter,
        repository=offer_repository,
        audit=audit,
    )
    transfers = SqlSpeechEvidenceTransferRepository()
    shared_relay: SemanticRelayService = get_semantic_relay_service()
    relay = SemanticSpeechRelay(
        relay=shared_relay,
        offers=offer_service,
        limits=SpeechRelayLimits(
            maximum_chunk_bytes=384 * 1024,
            maximum_in_flight_bytes=1024 * 1024,
        ),
        enforce_local_in_flight=False,
    )
    return SpeechEvidenceSyncComposition(
        service=HubSpeechEvidenceSyncService(
            membership=membership,
            consents=consents,
            epochs=epoch_adapter,
            keys=keys,
            replay=replay,
            offer_repository=offer_repository,
            offers=offer_service,
            transfers=transfers,
            relay=relay,
            control_relay=shared_relay,
        ),
        membership=membership,
        consents=consents,
        epochs=epoch_adapter,
        keys=keys,
        replay=replay,
        offers=offer_repository,
        transfers=transfers,
    )


def speech_evidence_sync_error(exc: Exception) -> HubSpeechEvidenceSyncError:
    if isinstance(exc, HubSpeechEvidenceSyncError):
        return exc
    if isinstance(exc, DataChannelContractError):
        return HubSpeechEvidenceSyncError(
            f"speech_evidence_relay_{exc.reason_code}",
            status_code=exc.status_code,
        )
    if isinstance(exc, SpeechEvidenceProtocolError):
        status = (
            413
            if exc.reason_code
            in {
                "speech_evidence_message_oversized",
                "speech_evidence_chunk_oversized",
            }
            else 409
            if exc.reason_code
            in {
                "speech_evidence_replayed",
                "speech_evidence_sequence_stale",
                "speech_evidence_epoch_stale",
                "speech_evidence_consent_stale",
            }
            else 422
        )
        return HubSpeechEvidenceSyncError(exc.reason_code, status_code=status)
    if isinstance(exc, (SpeechEvidenceOfferError, SpeechEvidenceSyncRepositoryError)):
        return HubSpeechEvidenceSyncError(exc.reason_code, status_code=exc.status_code)
    reason = str(getattr(exc, "reason_code", "speech_evidence_sync_unavailable"))
    status = int(getattr(exc, "status_code", 503))
    return HubSpeechEvidenceSyncError(reason, status_code=status)


def _transfer_participants(offer: SpeechEvidenceOfferRecord) -> tuple[str, str]:
    if offer.direction == "sender_to_receiver":
        return offer.sender_id, offer.recipient_id
    if offer.direction == "receiver_to_sender":
        return offer.recipient_id, offer.sender_id
    raise HubSpeechEvidenceSyncError("speech_evidence_direction_invalid", status_code=422)


def offer_public_dict(record: SpeechEvidenceOfferRecord) -> dict[str, object]:
    value = asdict(record)
    value.pop("tenant_id", None)
    return value


__all__ = [
    "HubSpeechEvidenceSyncError",
    "HubSpeechEvidenceSyncService",
    "PEER_CURATION_REQUEST_POLICY_DIGEST",
    "ShareSessionSpeechEvidenceEpoch",
    "ShareSessionSpeechEvidenceMembership",
    "SpeechEvidenceSyncComposition",
    "SqlSpeechEvidenceConsentAdapter",
    "build_speech_evidence_sync_composition",
    "offer_public_dict",
    "speech_evidence_sync_error",
]
