"""Hub control-plane service for epochs, sequences and replay protection."""

from __future__ import annotations

import base64
import binascii
import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from agent.repositories.webrtc_epoch_repository import (
    EpochClaimResult,
    WebrtcEpochRepository,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent

TRAFFIC_CLASSES = frozenset({"control", "media", "semantic", "bulk"})
REPLAY_WINDOW_SIZE = 128
MAX_PARTICIPANTS_PER_SCOPE = 256
REPLAY_TTL_SECONDS = 3600


@dataclass(frozen=True)
class ReplayDecision:
    accepted: bool
    reason_code: str


class WebrtcEpochService:
    def __init__(self, repository: WebrtcEpochRepository | None = None, *, clock=time.time) -> None:
        self._repository = repository or WebrtcEpochRepository()
        self._clock = clock

    def claim_epoch(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        hub_id: str,
        lease_seconds: int = 30,
        advance: bool = False,
        audit_event_factory: Callable[[int], SemanticMediaAuditEvent] | None = None,
        takeover_audit_event_factory: Callable[[int], SemanticMediaAuditEvent] | None = None,
    ) -> EpochClaimResult:
        if scope_kind not in {"session", "room"} or not _id(scope_id) or not _id(hub_id):
            return EpochClaimResult(False, "scope_invalid")
        if not 5 <= lease_seconds <= 300:
            return EpochClaimResult(False, "lease_invalid")
        return self._repository.claim(
            scope_kind=scope_kind,
            scope_id=scope_id,
            hub_id=hub_id,
            now=float(self._clock()),
            lease_seconds=lease_seconds,
            advance=advance,
            audit_event_factory=audit_event_factory,
            takeover_audit_event_factory=takeover_audit_event_factory,
        )

    def accept_sequence(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        epoch: int,
        sender_id: str,
        authenticated_sender_id: str,
        traffic_class: str,
        sequence: int,
        nonce_b64: str | None = None,
    ) -> ReplayDecision:
        if sender_id != authenticated_sender_id:
            return ReplayDecision(False, "sender_mismatch")
        if traffic_class not in TRAFFIC_CLASSES:
            return ReplayDecision(False, "traffic_class_invalid")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or not 1 <= sequence <= 9_007_199_254_740_991:
            return ReplayDecision(False, "sequence_invalid")
        nonce_digest: str | None = None
        if nonce_b64 is not None:
            try:
                nonce = base64.b64decode(nonce_b64, validate=True)
            except (ValueError, binascii.Error):
                return ReplayDecision(False, "nonce_invalid")
            if len(nonce) != 12:
                return ReplayDecision(False, "nonce_invalid")
            nonce_digest = hashlib.sha256(nonce).hexdigest()
        current = self._repository.get(scope_kind, scope_id)
        if current is None or current.closed_at is not None:
            return ReplayDecision(False, "scope_not_active")
        if epoch < current.epoch:
            return ReplayDecision(False, "epoch_stale")
        if epoch > current.epoch:
            return ReplayDecision(False, "epoch_future")
        now = float(self._clock())
        scope_key = f"{scope_kind}:{scope_id}"
        if self._repository.count_replay_senders(scope_key, now=now) >= MAX_PARTICIPANTS_PER_SCOPE:
            # Existing senders remain usable; the repository will update their
            # row, while a new sender is denied before allocating state.
            if not self._repository.has_replay_state(
                scope_key=scope_key,
                epoch=epoch,
                sender_id=sender_id,
                traffic_class=traffic_class,
            ):
                return ReplayDecision(False, "participant_budget_exceeded")
        accepted, reason = self._repository.update_replay(
            scope_key=scope_key,
            epoch=epoch,
            sender_id=sender_id,
            traffic_class=traffic_class,
            sequence=sequence,
            nonce_digest=nonce_digest,
            window_size=REPLAY_WINDOW_SIZE,
            expires_at=now + REPLAY_TTL_SECONDS,
            now=now,
        )
        return ReplayDecision(accepted, reason)

    def current_epoch(self, scope_kind: str, scope_id: str) -> int | None:
        row = self._repository.get(scope_kind, scope_id)
        if row is None or row.closed_at is not None:
            return None
        return int(row.epoch)

    def close_scope(self, scope_kind: str, scope_id: str) -> None:
        self._repository.close(scope_kind, scope_id, now=float(self._clock()))


def _id(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 128


__all__ = ["ReplayDecision", "WebrtcEpochService"]


_SERVICE = WebrtcEpochService()


def get_webrtc_epoch_service() -> WebrtcEpochService:
    return _SERVICE


__all__.append("get_webrtc_epoch_service")
