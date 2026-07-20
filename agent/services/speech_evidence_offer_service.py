"""Hub-owned bilateral speech-evidence offer authorization."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Mapping, Protocol

from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
    same_idempotent_audit_request,
)
from ananta_contracts.speech_evidence_sync import (
    GROUP_PREVIEW_VERSION,
    OFFER_PROTOCOL_VERSION,
    VerifiedSpeechEvidenceMessage,
    canonical_sha256,
    group_preview_comparison_digest,
    group_preview_group_id,
    group_preview_resolution_digest,
)


class SpeechEvidenceOfferError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class HubPeerAuthorization:
    tenant_id: str
    session_id: str
    pair_id: str
    peer_id: str
    audience_id: str
    epoch: int
    membership_version: int
    permissions: frozenset[str]
    active: bool


@dataclass(frozen=True)
class HubEvidenceConsent:
    peer_id: str
    pair_id: str
    version: int
    digest: str
    directions: frozenset[str]
    purposes: frozenset[str]
    data_classes: frozenset[str]
    fields: frozenset[str]
    trainer_classes: frozenset[str]
    maximum_retention_seconds: int
    expires_at_ms: int
    active: bool
    speaker_id: str = ""


class SpeechEvidenceMembershipPort(Protocol):
    def current(
        self,
        *,
        session_id: str,
        pair_id: str,
        peer_id: str,
        audience_id: str,
    ) -> HubPeerAuthorization | None: ...


class SpeechEvidenceConsentPort(Protocol):
    def current(self, *, pair_id: str, peer_id: str) -> HubEvidenceConsent | None: ...


class SpeechEvidenceEpochPort(Protocol):
    def current_epoch(self, *, session_id: str, pair_id: str) -> int | None: ...


@dataclass(frozen=True)
class SpeechEvidenceCandidateProjection:
    ordinal: int
    candidate_digest: str
    authority_digest: str
    revision: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SpeechEvidenceCandidateProjection:
        return cls(
            ordinal=int(value["ordinal"]),
            candidate_digest=str(value["candidate_digest"]),
            authority_digest=str(value["authority_digest"]),
            revision=int(value["revision"]),
        )

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechEvidenceGroupPreview:
    """Content-free, signed pre-acceptance binding for one evidence group."""

    preview_version: str
    group_id: str
    source_group_digest: str
    speaker_scope_digest: str
    quality_basis: str
    quality_digest: str
    resolution_digest: str
    original_candidates: tuple[SpeechEvidenceCandidateProjection, ...]
    resolution_state: str
    selected_candidate_digest: str | None
    unresolved_region_digests: tuple[str, ...]
    comparison_digest: str
    revision: int
    size_bytes: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SpeechEvidenceGroupPreview:
        return cls(
            preview_version=str(value["preview_version"]),
            group_id=str(value["group_id"]),
            source_group_digest=str(value["source_group_digest"]),
            speaker_scope_digest=str(value["speaker_scope_digest"]),
            quality_basis=str(value["quality_basis"]),
            quality_digest=str(value["quality_digest"]),
            resolution_digest=str(value["resolution_digest"]),
            original_candidates=tuple(
                SpeechEvidenceCandidateProjection.from_mapping(candidate)
                for candidate in value["original_candidates"]  # type: ignore[union-attr]
                if isinstance(candidate, Mapping)
            ),
            resolution_state=str(value["resolution_state"]),
            selected_candidate_digest=(
                str(value["selected_candidate_digest"])
                if value["selected_candidate_digest"] is not None
                else None
            ),
            unresolved_region_digests=tuple(str(item) for item in value["unresolved_region_digests"]),  # type: ignore[union-attr]
            comparison_digest=str(value["comparison_digest"]),
            revision=int(value["revision"]),
            size_bytes=int(value["size_bytes"]),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "preview_version": self.preview_version,
            "group_id": self.group_id,
            "source_group_digest": self.source_group_digest,
            "speaker_scope_digest": self.speaker_scope_digest,
            "quality_basis": self.quality_basis,
            "quality_digest": self.quality_digest,
            "resolution_digest": self.resolution_digest,
            "original_candidates": [value.public_dict() for value in self.original_candidates],
            "resolution_state": self.resolution_state,
            "selected_candidate_digest": self.selected_candidate_digest,
            "unresolved_region_digests": list(self.unresolved_region_digests),
            "comparison_digest": self.comparison_digest,
            "revision": self.revision,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SpeechEvidenceOfferRecord:
    offer_id: str
    proposal_verification_digest: str
    acceptance_verification_digest: str | None
    session_id: str
    pair_id: str
    epoch: int
    sender_id: str
    recipient_id: str
    inventory_root_digest: str
    direction: str
    purpose: str
    data_classes: tuple[str, ...]
    fields: tuple[str, ...]
    retention_seconds: int
    trainer_class: str
    group_ids: tuple[str, ...]
    total_bytes: int
    sender_consent_digest: str
    recipient_consent_digest: str
    scope_digest: str
    expires_at_ms: int
    state: str
    group_previews: tuple[SpeechEvidenceGroupPreview, ...] = ()
    group_preview_digest: str = ""
    transfer_started: bool = False
    invalidation_reason: str | None = None
    tenant_id: str = ""
    version: int = 1
    protocol_version: str = "ananta.speech-evidence-sync.v1"


class SpeechEvidenceOfferRepositoryPort(Protocol):
    def get(self, offer_id: str) -> SpeechEvidenceOfferRecord | None: ...

    def put_if_absent(
        self,
        record: SpeechEvidenceOfferRecord,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord: ...

    def compare_and_set(
        self,
        offer_id: str,
        *,
        expected_state: str,
        record: SpeechEvidenceOfferRecord,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord: ...


class InMemorySpeechEvidenceOfferRepository:
    def __init__(self) -> None:
        self._records: dict[str, SpeechEvidenceOfferRecord] = {}
        self._audit_events: dict[str, SemanticMediaAuditEvent] = {}
        self._lock = threading.RLock()

    def get(self, offer_id: str) -> SpeechEvidenceOfferRecord | None:
        with self._lock:
            return self._records.get(offer_id)

    def put_if_absent(
        self,
        record: SpeechEvidenceOfferRecord,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord:
        with self._lock:
            existing = self._records.setdefault(record.offer_id, record)
            if existing == record:
                self._stage_audit(audit_event)
            return existing

    def compare_and_set(
        self,
        offer_id: str,
        *,
        expected_state: str,
        record: SpeechEvidenceOfferRecord,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord:
        with self._lock:
            current = self._records.get(offer_id)
            if current is None:
                raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
            if current.state != expected_state:
                if current == record:
                    self._stage_audit(audit_event)
                    return current
                raise SpeechEvidenceOfferError("speech_evidence_offer_state_conflict", status_code=409)
            self._stage_audit(audit_event)
            self._records[offer_id] = record
            return record

    def _stage_audit(self, event: SemanticMediaAuditEvent | None) -> None:
        if event is None:
            return
        existing = self._audit_events.get(event.idempotency_digest)
        if existing is not None and not same_idempotent_audit_request(existing, event):
            raise SemanticMediaAuditError("audit_idempotency_conflict", status_code=409)
        self._audit_events[event.idempotency_digest] = event


class SpeechEvidenceOfferService:
    """Authorize offers without granting peers any task or dataset authority."""

    def __init__(
        self,
        *,
        membership: SpeechEvidenceMembershipPort,
        consents: SpeechEvidenceConsentPort,
        epochs: SpeechEvidenceEpochPort,
        repository: SpeechEvidenceOfferRepositoryPort,
        audit: SemanticMediaAuditPort | None = None,
        clock_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._membership = membership
        self._consents = consents
        self._epochs = epochs
        self._repository = repository
        self._audit = audit
        self._clock_ms = clock_ms

    def propose(self, message: VerifiedSpeechEvidenceMessage) -> SpeechEvidenceOfferRecord:
        payload = message.payload
        if message.header.message_type != "offer" or payload.get("stage") != "proposal":
            raise SpeechEvidenceOfferError("speech_evidence_offer_proposal_required")
        authorization = self._require_authority(message)
        consent = self._require_consent(
            message.header.pair_id,
            message.header.sender_id,
            tenant_id=authorization.tenant_id,
            session_id=message.header.session_id,
        )
        self._require_scope(payload, consent)
        previews = _previews(payload)
        self._require_preview_scope(message, payload, previews, consent)
        if message.header.consent_version != consent.version or payload.get("sender_consent_digest") != consent.digest:
            raise SpeechEvidenceOfferError("speech_evidence_offer_consent_stale", status_code=409)
        now = int(self._clock_ms())
        if message.header.expires_at_ms <= now or message.header.expires_at_ms > now + 5 * 60 * 1000:
            raise SpeechEvidenceOfferError("speech_evidence_offer_expired", status_code=410)
        record = SpeechEvidenceOfferRecord(
            offer_id=str(payload["offer_id"]),
            proposal_verification_digest=message.verification_digest,
            acceptance_verification_digest=None,
            session_id=message.header.session_id,
            pair_id=message.header.pair_id,
            epoch=message.header.epoch,
            sender_id=message.header.sender_id,
            recipient_id=message.header.audience_id,
            inventory_root_digest=str(payload["inventory_root_digest"]),
            direction=str(payload["direction"]),
            purpose=str(payload["purpose"]),
            data_classes=tuple(sorted(str(item) for item in payload["data_classes"])),
            fields=tuple(sorted(str(item) for item in payload["fields"])),
            retention_seconds=int(payload["retention_seconds"]),
            trainer_class=str(payload["trainer_class"]),
            group_ids=tuple(sorted(str(item) for item in payload["group_ids"])),
            group_previews=tuple(sorted(previews, key=lambda item: item.group_id)),
            group_preview_digest=group_preview_digest(previews),
            total_bytes=int(payload["total_bytes"]),
            sender_consent_digest=str(payload["sender_consent_digest"]),
            recipient_consent_digest=str(payload["recipient_consent_digest"]),
            scope_digest=str(payload["scope_digest"]),
            expires_at_ms=message.header.expires_at_ms,
            state="proposed",
            tenant_id=authorization.tenant_id,
            protocol_version=message.header.protocol_version,
        )
        existing = self._repository.put_if_absent(
            record,
            audit_event=self._audit_event(
                record,
                transition="offer_proposed",
                reason_code="peer_offer_verified",
                idempotency_key=f"speech-evidence-offer:propose:{message.verification_digest}",
            ),
        )
        if existing.proposal_verification_digest != record.proposal_verification_digest:
            raise SpeechEvidenceOfferError("speech_evidence_offer_id_conflict", status_code=409)
        return existing

    def accept(self, message: VerifiedSpeechEvidenceMessage) -> SpeechEvidenceOfferRecord:
        payload = message.payload
        if message.header.message_type != "offer" or payload.get("stage") != "acceptance":
            raise SpeechEvidenceOfferError("speech_evidence_offer_acceptance_required")
        proposed = self._repository.get(str(payload.get("offer_id") or ""))
        if proposed is None:
            raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
        if proposed.state == "accepted":
            if proposed.acceptance_verification_digest == message.verification_digest:
                return proposed
            raise SpeechEvidenceOfferError("speech_evidence_offer_replayed", status_code=409)
        if (
            message.header.session_id != proposed.session_id
            or message.header.pair_id != proposed.pair_id
            or message.header.epoch != proposed.epoch
            or message.header.sender_id != proposed.recipient_id
            or message.header.audience_id != proposed.sender_id
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_wrong_pair", status_code=403)
        self._require_authority(message)
        consent = self._require_consent(
            proposed.pair_id,
            proposed.recipient_id,
            tenant_id=proposed.tenant_id,
            session_id=proposed.session_id,
        )
        if (
            message.header.consent_version != consent.version
            or payload.get("recipient_consent_digest") != consent.digest
            or proposed.recipient_consent_digest != consent.digest
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_consent_stale", status_code=409)
        self._require_scope(payload, consent)
        self._require_reduction(proposed, payload)
        previews = _previews(payload)
        accepted = replace(
            proposed,
            acceptance_verification_digest=message.verification_digest,
            data_classes=tuple(sorted(str(item) for item in payload["data_classes"])),
            fields=tuple(sorted(str(item) for item in payload["fields"])),
            retention_seconds=int(payload["retention_seconds"]),
            trainer_class=str(payload["trainer_class"]),
            group_ids=tuple(sorted(str(item) for item in payload["group_ids"])),
            group_previews=tuple(sorted(previews, key=lambda item: item.group_id)),
            group_preview_digest=group_preview_digest(previews),
            total_bytes=int(payload["total_bytes"]),
            recipient_consent_digest=consent.digest,
            scope_digest=str(payload["scope_digest"]),
            expires_at_ms=min(proposed.expires_at_ms, message.header.expires_at_ms, consent.expires_at_ms),
            state="accepted",
        )
        return self._repository.compare_and_set(
            proposed.offer_id,
            expected_state="proposed",
            record=accepted,
            audit_event=self._audit_event(
                accepted,
                transition="offer_accepted",
                reason_code="peer_acceptance_verified",
                idempotency_key=f"speech-evidence-offer:accept:{message.verification_digest}",
            ),
        )

    def authorize_transfer(self, offer_id: str) -> SpeechEvidenceOfferRecord:
        record = self._repository.get(offer_id)
        if record is None:
            raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
        self._require_current(record)
        self._require_preview_record(record)
        if record.state != "accepted":
            raise SpeechEvidenceOfferError("speech_evidence_offer_not_accepted", status_code=409)
        if record.transfer_started:
            return record
        started = replace(record, transfer_started=True)
        return self._repository.compare_and_set(
            offer_id,
            expected_state="accepted",
            record=started,
            audit_event=self._audit_event(
                started,
                transition="transfer_authorized",
                reason_code="offer_authority_current",
                idempotency_key=f"speech-evidence-offer:transfer:{offer_id}:{record.version}",
            ),
        )

    def recover_current(self, offer_id: str) -> SpeechEvidenceOfferRecord:
        """Return an offer only while all original bilateral authority is current.

        Recovery is a read-only operation: unlike transfer authorization it
        accepts both proposed and accepted states and never advances the
        offer state machine.
        """

        record = self._repository.get(offer_id)
        if record is None:
            raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
        self._require_current(record)
        self._require_preview_record(record)
        return record

    def invalidate(self, offer_id: str, *, reason_code: str) -> SpeechEvidenceOfferRecord:
        record = self._repository.get(offer_id)
        if record is None:
            raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
        if record.state == "invalidated":
            return record
        invalidated = replace(record, state="invalidated", invalidation_reason=reason_code)
        reason_digest = hashlib.sha256(f"{offer_id}\0{reason_code}".encode()).hexdigest()
        return self._repository.compare_and_set(
            offer_id,
            expected_state=record.state,
            record=invalidated,
            audit_event=self._audit_event(
                invalidated,
                transition="offer_invalidated",
                reason_code=reason_code,
                idempotency_key=f"speech-evidence-offer:invalidate:{reason_digest}",
            ),
        )

    def _audit_event(
        self,
        record: SpeechEvidenceOfferRecord,
        *,
        transition: str,
        reason_code: str,
        idempotency_key: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=record.tenant_id,
                scope=f"speech-evidence:{record.session_id}",
                event_type="speech_evidence",
                transition=transition,
                reason_code=reason_code,
                epoch=record.epoch,
                job_ref=record.offer_id,
            )
        except Exception as exc:
            raise SpeechEvidenceOfferError(
                "speech_evidence_audit_unavailable",
                status_code=503,
            ) from exc

    def _require_authority(self, message: VerifiedSpeechEvidenceMessage) -> HubPeerAuthorization:
        return self._require_authority_fields(
            session_id=message.header.session_id,
            pair_id=message.header.pair_id,
            peer_id=message.header.sender_id,
            audience_id=message.header.audience_id,
            epoch=message.header.epoch,
        )

    def _require_authority_fields(
        self,
        *,
        session_id: str,
        pair_id: str,
        peer_id: str,
        audience_id: str,
        epoch: int,
    ) -> HubPeerAuthorization:
        authorization = self._membership.current(
            session_id=session_id,
            pair_id=pair_id,
            peer_id=peer_id,
            audience_id=audience_id,
        )
        if (
            authorization is None
            or not authorization.active
            or authorization.epoch != epoch
            or "peer_evidence_sync" not in authorization.permissions
        ):
            raise SpeechEvidenceOfferError("speech_evidence_membership_denied", status_code=403)
        current_epoch = self._epochs.current_epoch(
            session_id=session_id,
            pair_id=pair_id,
        )
        if current_epoch != epoch:
            raise SpeechEvidenceOfferError("speech_evidence_epoch_stale", status_code=409)
        return authorization

    def _require_consent(
        self,
        pair_id: str,
        peer_id: str,
        *,
        tenant_id: str = "",
        session_id: str = "",
    ) -> HubEvidenceConsent:
        scoped = getattr(self._consents, "current_scoped", None)
        consent = (
            scoped(
                tenant_id=tenant_id,
                session_id=session_id,
                pair_id=pair_id,
                peer_id=peer_id,
            )
            if callable(scoped)
            else self._consents.current(pair_id=pair_id, peer_id=peer_id)
        )
        if consent is None or not consent.active or consent.expires_at_ms <= int(self._clock_ms()):
            raise SpeechEvidenceOfferError("speech_evidence_consent_missing", status_code=403)
        return consent

    @staticmethod
    def _require_scope(payload: Mapping[str, object], consent: HubEvidenceConsent) -> None:
        if (
            payload.get("direction") not in consent.directions
            or payload.get("purpose") not in consent.purposes
            or not set(payload.get("data_classes", ())) <= consent.data_classes
            or not set(payload.get("fields", ())) <= consent.fields
            or payload.get("trainer_class") not in consent.trainer_classes
            or int(payload.get("retention_seconds", 0)) > consent.maximum_retention_seconds
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_scope_denied", status_code=403)

    @staticmethod
    def _require_reduction(proposed: SpeechEvidenceOfferRecord, payload: Mapping[str, object]) -> None:
        accepted_previews = {row.group_id: row for row in _previews(payload)}
        proposed_previews = {row.group_id: row for row in proposed.group_previews}
        if (
            payload.get("inventory_root_digest") != proposed.inventory_root_digest
            or payload.get("sender_consent_digest") != proposed.sender_consent_digest
            or payload.get("scope_digest") != proposed.scope_digest
            or payload.get("direction") != proposed.direction
            or payload.get("purpose") != proposed.purpose
            or not set(payload.get("data_classes", ())) <= set(proposed.data_classes)
            or not set(payload.get("fields", ())) <= set(proposed.fields)
            or not set(payload.get("group_ids", ())) <= set(proposed.group_ids)
            or set(accepted_previews) != set(payload.get("group_ids", ()))
            or any(proposed_previews.get(group_id) != preview for group_id, preview in accepted_previews.items())
            or int(payload.get("retention_seconds", 0)) > proposed.retention_seconds
            or int(payload.get("total_bytes", 0)) > proposed.total_bytes
            or (proposed.trainer_class == "none" and payload.get("trainer_class") != "none")
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_scope_expansion", status_code=403)

    @staticmethod
    def _require_preview_scope(
        message: VerifiedSpeechEvidenceMessage,
        payload: Mapping[str, object],
        previews: tuple[SpeechEvidenceGroupPreview, ...],
        consent: HubEvidenceConsent,
    ) -> None:
        if message.header.protocol_version != OFFER_PROTOCOL_VERSION or not previews:
            raise SpeechEvidenceOfferError("speech_evidence_offer_preview_required", status_code=422)
        speaker_id = consent.speaker_id or (
            message.header.sender_id
            if payload.get("direction") == "sender_to_receiver"
            else message.header.audience_id
        )
        expected_speaker = speech_evidence_speaker_scope_digest(
            pair_id=message.header.pair_id,
            epoch=message.header.epoch,
            speaker_id=speaker_id,
        )
        expected_quality = speech_evidence_quality_policy_digest()
        if any(
            preview.speaker_scope_digest != expected_speaker
            or preview.quality_basis != "policy"
            or preview.quality_digest != expected_quality
            for preview in previews
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_preview_scope_denied", status_code=403)

    @staticmethod
    def _require_preview_record(record: SpeechEvidenceOfferRecord) -> None:
        if (
            record.protocol_version != OFFER_PROTOCOL_VERSION
            or not record.group_previews
            or set(record.group_ids) != {row.group_id for row in record.group_previews}
            or sum(row.size_bytes for row in record.group_previews) != record.total_bytes
            or group_preview_digest(record.group_previews) != record.group_preview_digest
            or any(
                row.preview_version != GROUP_PREVIEW_VERSION
                or row.group_id != group_preview_group_id(row.source_group_digest, row.revision)
                or row.resolution_digest
                != group_preview_resolution_digest(row.source_group_digest, row.revision)
                or not _comparison_projection_valid(row)
                for row in record.group_previews
            )
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_preview_invalid", status_code=409)

    def _require_current(self, record: SpeechEvidenceOfferRecord) -> None:
        if record.expires_at_ms <= int(self._clock_ms()):
            raise SpeechEvidenceOfferError("speech_evidence_offer_expired", status_code=410)
        if self._epochs.current_epoch(session_id=record.session_id, pair_id=record.pair_id) != record.epoch:
            raise SpeechEvidenceOfferError("speech_evidence_epoch_stale", status_code=409)
        current_consents: dict[str, HubEvidenceConsent] = {}
        for peer_id, digest in (
            (record.sender_id, record.sender_consent_digest),
            (record.recipient_id, record.recipient_consent_digest),
        ):
            consent = self._require_consent(
                record.pair_id,
                peer_id,
                tenant_id=record.tenant_id,
                session_id=record.session_id,
            )
            if consent.digest != digest:
                raise SpeechEvidenceOfferError("speech_evidence_offer_consent_stale", status_code=409)
            current_consents[peer_id] = consent
        proposer_consent = current_consents[record.sender_id]
        speaker_id = proposer_consent.speaker_id or (
            record.sender_id if record.direction == "sender_to_receiver" else record.recipient_id
        )
        expected_speaker = speech_evidence_speaker_scope_digest(
            pair_id=record.pair_id,
            epoch=record.epoch,
            speaker_id=speaker_id,
        )
        expected_quality = speech_evidence_quality_policy_digest()
        if any(
            preview.speaker_scope_digest != expected_speaker
            or preview.quality_basis != "policy"
            or preview.quality_digest != expected_quality
            for preview in record.group_previews
        ):
            raise SpeechEvidenceOfferError(
                "speech_evidence_offer_preview_scope_denied",
                status_code=403,
            )
        self._require_authority_fields(
            session_id=record.session_id,
            pair_id=record.pair_id,
            peer_id=record.sender_id,
            audience_id=record.recipient_id,
            epoch=record.epoch,
        )


def offer_scope_digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _previews(payload: Mapping[str, object]) -> tuple[SpeechEvidenceGroupPreview, ...]:
    raw = payload.get("group_previews")
    if not isinstance(raw, list) or not raw:
        raise SpeechEvidenceOfferError("speech_evidence_offer_preview_required", status_code=422)
    try:
        previews = tuple(
            SpeechEvidenceGroupPreview.from_mapping(value)
            for value in raw
            if isinstance(value, Mapping)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeechEvidenceOfferError("speech_evidence_offer_preview_invalid", status_code=422) from exc
    if len(previews) != len(raw):
        raise SpeechEvidenceOfferError("speech_evidence_offer_preview_invalid", status_code=422)
    for preview in previews:
        if (
            preview.preview_version != GROUP_PREVIEW_VERSION
            or preview.group_id != group_preview_group_id(preview.source_group_digest, preview.revision)
            or preview.resolution_digest
            != group_preview_resolution_digest(preview.source_group_digest, preview.revision)
            or not _comparison_projection_valid(preview)
        ):
            raise SpeechEvidenceOfferError("speech_evidence_offer_preview_invalid", status_code=422)
    return previews


def _comparison_projection_valid(preview: SpeechEvidenceGroupPreview) -> bool:
    candidates = preview.original_candidates
    candidate_digests = [value.candidate_digest for value in candidates]
    if (
        not 1 <= len(candidates) <= 32
        or [value.ordinal for value in candidates] != list(range(1, len(candidates) + 1))
        or len(candidate_digests) != len(set(candidate_digests))
        or preview.unresolved_region_digests != tuple(sorted(set(preview.unresolved_region_digests)))
    ):
        return False
    if preview.resolution_state == "resolved":
        if preview.selected_candidate_digest not in candidate_digests or preview.unresolved_region_digests:
            return False
    elif preview.resolution_state == "unresolved":
        if preview.selected_candidate_digest is not None or not preview.unresolved_region_digests:
            return False
    else:
        return False
    return preview.comparison_digest == group_preview_comparison_digest(
        source_group_digest=preview.source_group_digest,
        revision=preview.revision,
        original_candidates=[value.public_dict() for value in candidates],
        resolution_state=preview.resolution_state,
        selected_candidate_digest=preview.selected_candidate_digest,
        unresolved_region_digests=list(preview.unresolved_region_digests),
    )


def group_preview_digest(previews: tuple[SpeechEvidenceGroupPreview, ...]) -> str:
    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-group-preview-set.v1",
            "groups": [row.public_dict() for row in sorted(previews, key=lambda item: item.group_id)],
        }
    )


def speech_evidence_speaker_scope_digest(*, pair_id: str, epoch: int, speaker_id: str) -> str:
    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-speaker-scope.v1",
            "epoch": epoch,
            "pair_id": pair_id,
            "speaker_id": speaker_id,
        }
    )


def speech_evidence_quality_policy_digest() -> str:
    return canonical_sha256(
        {
            "accepted_source_states": ["corrected", "correction_failed", "final"],
            "domain": "ananta.speech-evidence-quality-policy.v1",
            "policy": "final-or-reviewed-transcript",
        }
    )


__all__ = [
    "HubEvidenceConsent",
    "HubPeerAuthorization",
    "InMemorySpeechEvidenceOfferRepository",
    "SpeechEvidenceConsentPort",
    "SpeechEvidenceEpochPort",
    "SpeechEvidenceGroupPreview",
    "SpeechEvidenceMembershipPort",
    "SpeechEvidenceOfferError",
    "SpeechEvidenceOfferRecord",
    "SpeechEvidenceOfferRepositoryPort",
    "SpeechEvidenceOfferService",
    "group_preview_digest",
    "offer_scope_digest",
    "speech_evidence_quality_policy_digest",
    "speech_evidence_speaker_scope_digest",
]
