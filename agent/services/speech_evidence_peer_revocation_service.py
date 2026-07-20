"""Bounded, honest remote revocation with immediate local fencing."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol

from ananta_contracts.speech_evidence_sync import VerifiedSpeechEvidenceMessage


class SpeechEvidenceRevocationError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class LocalSpeechEvidenceFencePort(Protocol):
    def fence(
        self,
        *,
        pair_id: str,
        group_ids: tuple[str, ...],
        revocation_epoch: int,
        reason_code: str,
    ) -> str: ...


class SpeechEvidenceRevocationSignerPort(Protocol):
    def sign_revocation(self, payload: Mapping[str, object], *, expires_at_ms: int) -> Mapping[str, object]: ...


class SpeechEvidenceRevocationTransportPort(Protocol):
    def send(self, message: Mapping[str, object]) -> bool: ...


@dataclass(frozen=True)
class PeerRevocationRecord:
    revocation_id: str
    pair_id: str
    sender_id: str
    audience_id: str
    scope_digest: str
    group_ids: tuple[str, ...]
    reason_code: str
    requested_action: str
    revocation_epoch: int
    created_at_ms: int
    deadline_at_ms: int
    local_impact_digest: str
    state: str
    attempts: int = 0
    next_attempt_at_ms: int = 0
    acknowledged_group_ids: tuple[str, ...] = ()
    unresolved_group_ids: tuple[str, ...] = ()
    remote_impact_digest: str | None = None
    last_reason_code: str | None = None

    def public_dict(self) -> dict[str, object]:
        return {
            "revocation_id": self.revocation_id,
            "pair_id": self.pair_id,
            "audience_id_digest": hashlib.sha256(self.audience_id.encode()).hexdigest(),
            "scope_digest": self.scope_digest,
            "group_count": len(self.group_ids),
            "reason_code": self.reason_code,
            "requested_action": self.requested_action,
            "revocation_epoch": self.revocation_epoch,
            "created_at_ms": self.created_at_ms,
            "deadline_at_ms": self.deadline_at_ms,
            "local_impact_digest": self.local_impact_digest,
            "state": self.state,
            "attempts": self.attempts,
            "acknowledged_count": len(self.acknowledged_group_ids),
            "unresolved_count": len(self.unresolved_group_ids),
            "remote_impact_digest": self.remote_impact_digest,
            "last_reason_code": self.last_reason_code,
        }


class SpeechEvidencePeerRevocationService:
    def __init__(
        self,
        *,
        local_fence: LocalSpeechEvidenceFencePort,
        signer: SpeechEvidenceRevocationSignerPort,
        transport: SpeechEvidenceRevocationTransportPort,
        maximum_attempts: int = 5,
        retry_interval_ms: int = 10_000,
        maximum_duration_ms: int = 5 * 60_000,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        audit_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        if not 1 <= maximum_attempts <= 20 or not 1_000 <= retry_interval_ms <= 60_000:
            raise ValueError("speech_evidence_revocation_retry_policy_invalid")
        if not retry_interval_ms <= maximum_duration_ms <= 24 * 60 * 60 * 1000:
            raise ValueError("speech_evidence_revocation_duration_invalid")
        self._fence = local_fence
        self._signer = signer
        self._transport = transport
        self._maximum_attempts = maximum_attempts
        self._retry_interval_ms = retry_interval_ms
        self._maximum_duration_ms = maximum_duration_ms
        self._clock_ms = clock_ms
        self._audit = audit_sink or (lambda _event, _details: None)
        self._records: dict[str, PeerRevocationRecord] = {}
        self._lock = threading.RLock()

    def request(
        self,
        *,
        pair_id: str,
        sender_id: str,
        audience_id: str,
        scope_digest: str,
        group_ids: tuple[str, ...],
        reason_code: str,
        requested_action: str,
        revocation_epoch: int,
        idempotency_key: str,
    ) -> PeerRevocationRecord:
        if not group_ids or len(group_ids) > 4096 or len(group_ids) != len(set(group_ids)):
            raise SpeechEvidenceRevocationError("speech_evidence_revocation_groups_invalid")
        if requested_action not in {"delete", "stop_use"}:
            raise SpeechEvidenceRevocationError("speech_evidence_revocation_action_invalid")
        now = int(self._clock_ms())
        revocation_id = hashlib.sha256(
            f"{pair_id}\0{sender_id}\0{audience_id}\0{idempotency_key}".encode()
        ).hexdigest()
        with self._lock:
            existing = self._records.get(revocation_id)
            if existing is not None:
                if existing.group_ids != tuple(sorted(group_ids)) or existing.scope_digest != scope_digest:
                    raise SpeechEvidenceRevocationError("speech_evidence_revocation_id_conflict", status_code=409)
                return existing
            # Local enforcement is deliberately before any remote network call.
            local_impact = self._fence.fence(
                pair_id=pair_id,
                group_ids=tuple(sorted(group_ids)),
                revocation_epoch=revocation_epoch,
                reason_code=reason_code,
            )
            record = PeerRevocationRecord(
                revocation_id=revocation_id,
                pair_id=pair_id,
                sender_id=sender_id,
                audience_id=audience_id,
                scope_digest=scope_digest,
                group_ids=tuple(sorted(group_ids)),
                reason_code=reason_code,
                requested_action=requested_action,
                revocation_epoch=revocation_epoch,
                created_at_ms=now,
                deadline_at_ms=now + self._maximum_duration_ms,
                local_impact_digest=local_impact,
                state="pending",
                next_attempt_at_ms=now,
            )
            self._records[revocation_id] = record
        self._emit("speech_evidence_revocation_created", record, "local_fence_applied")
        return self.tick(revocation_id)

    def tick(self, revocation_id: str) -> PeerRevocationRecord:
        with self._lock:
            record = self._require(revocation_id)
            now = int(self._clock_ms())
            if record.state in {"acknowledged", "resolved_late", "cancelled"}:
                return record
            if now >= record.deadline_at_ms or record.attempts >= self._maximum_attempts:
                unresolved = tuple(sorted(set(record.group_ids) - set(record.acknowledged_group_ids)))
                record = replace(
                    record,
                    state="unresolved",
                    unresolved_group_ids=unresolved,
                    last_reason_code="speech_evidence_remote_ack_unresolved",
                )
                self._records[revocation_id] = record
                self._emit("speech_evidence_revocation_unresolved", record, record.last_reason_code)
                return record
            if now < record.next_attempt_at_ms:
                return record
            payload = {
                "traffic_class": "control",
                "revocation_id": record.revocation_id,
                "group_ids": list(record.group_ids),
                "scope_digest": record.scope_digest,
                "reason_code": record.reason_code,
                "revocation_epoch": record.revocation_epoch,
                "deadline_at_ms": record.deadline_at_ms,
                "requested_action": record.requested_action,
            }
            message = self._signer.sign_revocation(payload, expires_at_ms=record.deadline_at_ms)
            sent = self._transport.send(message)
            record = replace(
                record,
                attempts=record.attempts + 1,
                next_attempt_at_ms=now + self._retry_interval_ms,
                last_reason_code=None if sent else "speech_evidence_peer_offline",
            )
            self._records[revocation_id] = record
            self._emit(
                "speech_evidence_revocation_sent" if sent else "speech_evidence_revocation_deferred",
                record,
                record.last_reason_code or "sent",
            )
            return record

    def acknowledge(self, message: VerifiedSpeechEvidenceMessage) -> PeerRevocationRecord:
        if message.header.message_type != "revocation_ack":
            raise SpeechEvidenceRevocationError("speech_evidence_revocation_ack_required")
        payload = message.payload
        with self._lock:
            record = self._require(str(payload.get("revocation_id") or ""))
            if (
                message.header.pair_id != record.pair_id
                or message.header.sender_id != record.audience_id
                or message.header.audience_id != record.sender_id
                or payload.get("scope_digest") != record.scope_digest
                or payload.get("revocation_epoch") != record.revocation_epoch
            ):
                raise SpeechEvidenceRevocationError("speech_evidence_revocation_ack_binding_mismatch", status_code=403)
            results = payload.get("group_results")
            assert isinstance(results, list)
            result_ids = {str(item["group_id"]) for item in results}
            if not result_ids <= set(record.group_ids):
                raise SpeechEvidenceRevocationError("speech_evidence_revocation_ack_groups_invalid")
            if (
                record.state in {"acknowledged", "resolved_late"}
                and record.remote_impact_digest == payload.get("impact_digest")
                and set(record.acknowledged_group_ids) == set(record.group_ids)
            ):
                return record
            terminal = {
                str(item["group_id"])
                for item in results
                if item.get("state") in {"deleted", "use_stopped", "not_found"}
            }
            acknowledged = tuple(sorted(set(record.acknowledged_group_ids) | terminal))
            unresolved = tuple(sorted(set(record.group_ids) - set(acknowledged)))
            complete = not unresolved and payload.get("decision") == "complete"
            state = (
                "resolved_late"
                if complete and record.state == "unresolved"
                else "acknowledged"
                if complete
                else "partial_ack"
            )
            updated = replace(
                record,
                state=state,
                acknowledged_group_ids=acknowledged,
                unresolved_group_ids=unresolved,
                remote_impact_digest=str(payload["impact_digest"]),
                last_reason_code=None if complete else "speech_evidence_remote_ack_partial",
            )
            self._records[record.revocation_id] = updated
        self._emit("speech_evidence_revocation_acknowledged", updated, updated.last_reason_code or state)
        return updated

    def status(self, revocation_id: str) -> Mapping[str, object]:
        with self._lock:
            return self._require(revocation_id).public_dict()

    def _require(self, revocation_id: str) -> PeerRevocationRecord:
        record = self._records.get(revocation_id)
        if record is None:
            raise SpeechEvidenceRevocationError("speech_evidence_revocation_not_found", status_code=404)
        return record

    def _emit(self, event: str, record: PeerRevocationRecord, reason: str) -> None:
        self._audit(
            event,
            {
                "revocation_id": record.revocation_id,
                "pair_id": record.pair_id,
                "group_count": len(record.group_ids),
                "revocation_epoch": record.revocation_epoch,
                "state": record.state,
                "attempts": record.attempts,
                "reason_code": reason,
            },
        )


__all__ = [
    "LocalSpeechEvidenceFencePort",
    "PeerRevocationRecord",
    "SpeechEvidencePeerRevocationService",
    "SpeechEvidenceRevocationError",
    "SpeechEvidenceRevocationSignerPort",
    "SpeechEvidenceRevocationTransportPort",
]
