"""Hub-owned authorization and storage service for opaque relay envelopes."""

from __future__ import annotations

import base64
import time
from dataclasses import asdict
from typing import Callable, Protocol

from agent.repositories.semantic_relay_repository import SemanticRelayEnvelope, SemanticRelayRepository
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.semantic_relay_authorization import SemanticRelayAuthorization
from agent.services.semantic_relay_limits import DEFAULT_SEMANTIC_RELAY_LIMITS, SemanticRelayLimits
from agent.services.semantic_relay_observability import SemanticRelayObservability
from agent.services.semantic_relay_rate_limiter import SemanticRelayPollLimiter
from ananta_contracts.webrtc_datachannel import (
    ValidatedDataChannelMessage,
    parse_message,
    parse_wire_message,
)

PERMISSION_BY_TRAFFIC_CLASS = {
    "control": "semantic_control",
    "transcript": "semantic_speech_receive",
    "audio_recovery": "semantic_speech_recovery",
    "visual_semantic": "semantic_visual_receive",
    "evidence_bulk": "peer_evidence_sync",
    "diagnostic": "semantic_diagnostics",
}


class SemanticRelayReplayPort(Protocol):
    def decide(
        self,
        *,
        session_id: str,
        epoch: int,
        sender_id: str,
        traffic_class: str,
        sequence: int,
    ) -> tuple[bool, str]: ...


class SemanticRelayTrafficPolicyPort(Protocol):
    def enabled(self, traffic_class: str) -> bool: ...


class SemanticRelayKeyConfirmationPort(Protocol):
    def confirmed(
        self,
        *,
        session_id: str,
        epoch: int,
        sender_id: str,
        audience_id: str,
        now: float,
    ) -> bool: ...


class SemanticRelayServiceError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


class SemanticRelayService:
    def __init__(
        self,
        *,
        repository: SemanticRelayRepository,
        authorization: SemanticRelayAuthorization,
        limits: SemanticRelayLimits = DEFAULT_SEMANTIC_RELAY_LIMITS,
        observability: SemanticRelayObservability | None = None,
        poll_limiter: SemanticRelayPollLimiter | None = None,
        replay: SemanticRelayReplayPort | None = None,
        traffic_policy: SemanticRelayTrafficPolicyPort | None = None,
        key_confirmation: SemanticRelayKeyConfirmationPort | None = None,
        audit: SemanticMediaAuditPort | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        limits.validate()
        self._repository = repository
        self._authorization = authorization
        self._limits = limits
        self._observability = observability or SemanticRelayObservability()
        self._poll_limiter = poll_limiter or SemanticRelayPollLimiter(
            max_per_minute=limits.max_poll_per_minute,
        )
        self._replay = replay
        self._traffic_policy = traffic_policy
        self._key_confirmation = key_confirmation
        self._audit = audit
        self._clock = clock

    def append_raw(self, *, tenant_id: str, authenticated_sender_id: str, raw: bytes) -> dict:
        if len(raw) > self._limits.max_request_bytes:
            raise SemanticRelayServiceError("relay_request_too_large", status_code=413)
        message = parse_message(raw)
        return self.append_message(
            tenant_id=tenant_id,
            authenticated_sender_id=authenticated_sender_id,
            message=message,
        )

    def append_wire(
        self,
        *,
        tenant_id: str,
        authenticated_sender_id: str,
        expected_session_id: str,
        raw: bytes,
    ) -> dict:
        if len(raw) > self._limits.max_request_bytes:
            raise SemanticRelayServiceError("relay_request_too_large", status_code=413)
        message = parse_wire_message(raw)
        return self.append_message(
            tenant_id=tenant_id,
            authenticated_sender_id=authenticated_sender_id,
            message=message,
            expected_session_id=expected_session_id,
        )

    def append_message(
        self,
        *,
        tenant_id: str,
        authenticated_sender_id: str,
        message: ValidatedDataChannelMessage,
        expected_session_id: str | None = None,
    ) -> dict:
        if expected_session_id is not None and message.session_id != expected_session_id:
            raise SemanticRelayServiceError("relay_session_mismatch", status_code=403)
        if authenticated_sender_id != message.sender_id:
            raise SemanticRelayServiceError("relay_sender_mismatch", status_code=403)
        now = float(self._clock())
        if message.expires_at_ms <= int(now * 1000):
            raise SemanticRelayServiceError("relay_envelope_expired", status_code=410)
        if message.expires_at_ms > int((now + self._limits.retention_seconds) * 1000):
            raise SemanticRelayServiceError("relay_retention_exceeded", status_code=422)
        self._require_traffic_enabled(message.traffic_class)
        permission = PERMISSION_BY_TRAFFIC_CLASS.get(message.traffic_class)
        if permission is None:
            raise SemanticRelayServiceError("relay_traffic_class_unknown")
        self._authorization.require_send(
            tenant_id=tenant_id,
            session_id=message.session_id,
            sender_id=authenticated_sender_id,
            audience_id=message.audience_id,
            epoch=message.epoch,
            required_permission=permission,
        )
        if self._key_confirmation is None:
            raise SemanticRelayServiceError("relay_key_confirmation_guard_unavailable", status_code=503)
        if not self._key_confirmation.confirmed(
            session_id=message.session_id,
            epoch=message.epoch,
            sender_id=message.sender_id,
            audience_id=message.audience_id,
            now=now,
        ):
            raise SemanticRelayServiceError("key_confirmation_required", status_code=409)
        if self._replay is None:
            raise SemanticRelayServiceError("relay_replay_guard_unavailable", status_code=503)
        accepted, replay_reason = self._replay.decide(
            session_id=message.session_id,
            epoch=message.epoch,
            sender_id=message.sender_id,
            traffic_class=message.traffic_class,
            sequence=message.sequence,
        )
        if not accepted:
            raise SemanticRelayServiceError(replay_reason, status_code=409)
        envelope = SemanticRelayEnvelope(
            message_id=message.message_id,
            tenant_id=tenant_id,
            session_id=message.session_id,
            epoch=message.epoch,
            sender_id=message.sender_id,
            audience_id=message.audience_id,
            traffic_class=message.traffic_class,
            sequence=message.sequence,
            compression=message.compression,
            security_algorithm=str(message.security["algorithm"]),
            key_id=str(message.security["key_id"]),
            payload_bytes=len(message.ciphertext),
            payload_digest=message.payload_digest,
            ciphertext=base64.b64encode(message.ciphertext).decode("ascii"),
            expires_at=message.expires_at_ms / 1000.0,
        )
        audit_event = self._prepare_audit(
            idempotency_key=f"semantic-relay:queued:{envelope.message_id}",
            tenant_id=tenant_id,
            session_id=envelope.session_id,
            epoch=envelope.epoch,
            transition="queued",
            reason_code="accepted",
            job_ref=envelope.payload_digest,
        )
        stored = self._repository.append(envelope, now=now, audit_event=audit_event)
        self._observability.emit(
            direction="outbound",
            traffic_class=stored.traffic_class,
            state="queued",
            reason_code="accepted",
        )
        return self._public(stored)

    def read_after(
        self,
        *,
        tenant_id: str,
        audience_id: str,
        session_id: str,
        epoch: int,
        traffic_class: str,
        cursor: int,
        limit: int = 50,
    ) -> dict:
        permission = PERMISSION_BY_TRAFFIC_CLASS.get(traffic_class)
        if permission is None:
            raise SemanticRelayServiceError("relay_traffic_class_unknown")
        self._require_traffic_enabled(traffic_class)
        self._authorization.require_read(
            tenant_id=tenant_id,
            session_id=session_id,
            audience_id=audience_id,
            epoch=epoch,
            required_permission=permission,
        )
        now = float(self._clock())
        if not self._poll_limiter.allow(
            tenant_id=tenant_id,
            session_id=session_id,
            audience_id=audience_id,
            now=now,
        ):
            self._observability.emit(
                direction="inbound",
                traffic_class=traffic_class,
                state="dropped",
                reason_code="quota_exceeded",
            )
            raise SemanticRelayServiceError("relay_poll_rate_limited", status_code=429)
        rows = [
            row
            for row in self._repository.read_after(
                tenant_id=tenant_id,
                session_id=session_id,
                audience_id=audience_id,
                cursor=max(0, int(cursor)),
                limit=max(1, min(int(limit), self._limits.max_batch_count)),
                now=now,
                traffic_class=traffic_class,
            )
            if row.epoch == epoch and row.traffic_class == traffic_class
        ]
        next_cursor = max([max(0, int(cursor)), *(row.cursor for row in rows)])
        return {"messages": [self._public(row) for row in rows], "cursor": next_cursor}

    def acknowledge(
        self,
        *,
        tenant_id: str,
        audience_id: str,
        session_id: str,
        epoch: int,
        traffic_class: str,
        cursor: int,
    ) -> int:
        permission = PERMISSION_BY_TRAFFIC_CLASS.get(traffic_class)
        if permission is None:
            raise SemanticRelayServiceError("relay_traffic_class_unknown")
        self._require_traffic_enabled(traffic_class)
        self._authorization.require_read(
            tenant_id=tenant_id,
            session_id=session_id,
            audience_id=audience_id,
            epoch=epoch,
            required_permission=permission,
        )
        audit_event = self._prepare_audit(
            idempotency_key=f"semantic-relay:ack:{session_id}:{audience_id}:{traffic_class}:{cursor}",
            tenant_id=tenant_id,
            session_id=session_id,
            epoch=epoch,
            transition="acknowledged",
            reason_code="audience_confirmed",
            job_ref=f"{audience_id}:{traffic_class}:{cursor}",
        )
        acknowledged = self._repository.acknowledge(
            tenant_id=tenant_id,
            session_id=session_id,
            audience_id=audience_id,
            cursor=max(0, int(cursor)),
            now=float(self._clock()),
            traffic_class=traffic_class,
            audit_event=audit_event,
        )
        return acknowledged

    def revoke(
        self,
        *,
        tenant_id: str,
        session_id: str,
        message_id: str | None = None,
        epoch: int | None = None,
    ) -> int:
        audit_event = (
            self._prepare_audit(
                idempotency_key=f"semantic-relay:revoke:{session_id}:{message_id or 'all'}",
                tenant_id=tenant_id,
                session_id=session_id,
                epoch=epoch,
                transition="revoked",
                reason_code="hub_revoked",
                job_ref=message_id or session_id,
            )
            if epoch is not None
            else None
        )
        revoked = self._repository.revoke(
            tenant_id=tenant_id,
            session_id=session_id,
            message_id=message_id,
            audit_event=audit_event,
        )
        return revoked

    def _prepare_audit(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        session_id: str,
        epoch: int | None,
        transition: str,
        reason_code: str,
        job_ref: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=tenant_id,
                scope=f"semantic-media-session:{session_id}",
                event_type="semantic_relay",
                transition=transition,
                reason_code=reason_code,
                epoch=max(1, int(epoch or 1)),
                job_ref=job_ref,
            )
        except Exception as exc:
            raise SemanticRelayServiceError("semantic_audit_unavailable", status_code=503) from exc

    def _require_traffic_enabled(self, traffic_class: str) -> None:
        if self._traffic_policy is None:
            raise SemanticRelayServiceError("relay_traffic_policy_unavailable", status_code=503)
        if not self._traffic_policy.enabled(traffic_class):
            raise SemanticRelayServiceError("semantic_feature_disabled", status_code=403)

    @staticmethod
    def _public(envelope: SemanticRelayEnvelope) -> dict:
        value = asdict(envelope)
        value.pop("tenant_id", None)
        value["expires_at_ms"] = int(envelope.expires_at * 1000)
        value["security"] = {
            "algorithm": value.pop("security_algorithm"),
            "key_id": value.pop("key_id"),
        }
        value["version"] = "ananta.webrtc-datachannel.v1"
        value.pop("expires_at", None)
        value.pop("created_at", None)
        return value


__all__ = [
    "PERMISSION_BY_TRAFFIC_CLASS",
    "SemanticRelayService",
    "SemanticRelayServiceError",
    "SemanticRelayReplayPort",
    "SemanticRelayTrafficPolicyPort",
    "SemanticRelayKeyConfirmationPort",
]
