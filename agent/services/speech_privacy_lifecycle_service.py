"""Hub-owned, phase-aware speech revocation coordinator with honest remote state."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, replace
from typing import Protocol

SPEECH_DATA_PHASES = frozenset(
    {
        "capture",
        "buffer",
        "transfer",
        "quarantine",
        "curation",
        "dataset",
        "reconciliation",
        "training",
        "evaluation",
        "approval",
        "inference",
    }
)
SAFE_STATE_BY_PHASE = {
    "capture": "capture_stopped",
    "buffer": "buffer_deleted",
    "transfer": "transfer_fenced",
    "quarantine": "quarantine_deleted",
    "curation": "curation_cancelled",
    "dataset": "dataset_version_revoked",
    "reconciliation": "resolution_quarantined",
    "training": "training_fenced",
    "evaluation": "evaluation_fenced",
    "approval": "approval_revoked",
    "inference": "adapter_fenced",
}


class SpeechPrivacyLifecycleError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class SpeechPrivacyTombstone:
    scope_digest: str
    evidence_digest: str
    phase: str
    revocation_epoch: int
    safe_state: str
    local_fenced: bool
    key_destroyed: bool
    remote_state: str
    remote_request_digest: str | None
    remote_ack_digest: str | None

    def public(self) -> dict[str, object]:
        return {
            "scope_digest": self.scope_digest,
            "evidence_digest": self.evidence_digest,
            "phase": self.phase,
            "revocation_epoch": self.revocation_epoch,
            "safe_state": self.safe_state,
            "local_fenced": self.local_fenced,
            "key_destroyed": self.key_destroyed,
            "remote_state": self.remote_state,
            "remote_request_digest": self.remote_request_digest,
            "remote_ack_digest": self.remote_ack_digest,
        }


class SpeechPrivacyFencePort(Protocol):
    def fence(
        self,
        *,
        scope_digest: str,
        evidence_digest: str,
        phase: str,
        revocation_epoch: int,
    ) -> bool: ...


class SpeechPrivacyKeyPort(Protocol):
    def destroy(
        self,
        *,
        scope_digest: str,
        evidence_digest: str,
        revocation_epoch: int,
    ) -> bool: ...


class SpeechPrivacyRemotePort(Protocol):
    def stage(
        self,
        *,
        evidence_digest: str,
        request_digest: str,
        revocation_epoch: int,
    ) -> bool: ...

    def acknowledge(
        self,
        *,
        evidence_digest: str,
        request_digest: str,
        ack_digest: str,
        signature_verified: bool,
    ) -> bool: ...


class SpeechPrivacyTombstoneRepository(Protocol):
    def get(self, evidence_digest: str) -> SpeechPrivacyTombstone | None: ...

    def put_once(self, value: SpeechPrivacyTombstone) -> tuple[SpeechPrivacyTombstone, bool]: ...

    def replace(self, value: SpeechPrivacyTombstone) -> SpeechPrivacyTombstone: ...


class InMemorySpeechPrivacyTombstoneRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rows: dict[str, SpeechPrivacyTombstone] = {}

    def get(self, evidence_digest: str) -> SpeechPrivacyTombstone | None:
        with self._lock:
            return self._rows.get(evidence_digest)

    def put_once(self, value: SpeechPrivacyTombstone) -> tuple[SpeechPrivacyTombstone, bool]:
        with self._lock:
            previous = self._rows.get(value.evidence_digest)
            if previous is not None:
                if (
                    previous.scope_digest != value.scope_digest
                    or previous.phase != value.phase
                    or previous.revocation_epoch != value.revocation_epoch
                    or previous.remote_request_digest != value.remote_request_digest
                ):
                    raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_conflict")
                return previous, False
            self._rows[value.evidence_digest] = value
            return value, True

    def replace(self, value: SpeechPrivacyTombstone) -> SpeechPrivacyTombstone:
        with self._lock:
            if value.evidence_digest not in self._rows:
                raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_missing")
            self._rows[value.evidence_digest] = value
            return value


class SpeechPrivacyLifecycleService:
    def __init__(
        self,
        *,
        fences: SpeechPrivacyFencePort,
        keys: SpeechPrivacyKeyPort,
        remote: SpeechPrivacyRemotePort,
        tombstones: SpeechPrivacyTombstoneRepository,
    ) -> None:
        self._fences = fences
        self._keys = keys
        self._remote = remote
        self._tombstones = tombstones

    def revoke(
        self,
        *,
        scope_digest: str,
        evidence_digest: str,
        phase: str,
        revocation_epoch: int,
        remote_required: bool,
    ) -> tuple[SpeechPrivacyTombstone, bool]:
        _digest(scope_digest)
        _digest(evidence_digest)
        if phase not in SPEECH_DATA_PHASES or revocation_epoch < 1:
            raise SpeechPrivacyLifecycleError("speech_privacy_revoke_scope_invalid")
        previous = self._tombstones.get(evidence_digest)
        if previous is not None:
            if (
                previous.scope_digest != scope_digest
                or previous.phase != phase
                or previous.revocation_epoch != revocation_epoch
                or (previous.remote_request_digest is not None) != remote_required
            ):
                raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_conflict")
            return previous, False
        # Local safety is committed before any remote request is represented.
        local_fenced = self._fences.fence(
            scope_digest=scope_digest,
            evidence_digest=evidence_digest,
            phase=phase,
            revocation_epoch=revocation_epoch,
        )
        key_destroyed = self._keys.destroy(
            scope_digest=scope_digest,
            evidence_digest=evidence_digest,
            revocation_epoch=revocation_epoch,
        )
        if not local_fenced or not key_destroyed:
            raise SpeechPrivacyLifecycleError("speech_privacy_local_fence_failed")
        request_digest = (
            hashlib.sha256(f"remote:{scope_digest}:{evidence_digest}:{revocation_epoch}".encode()).hexdigest()
            if remote_required
            else None
        )
        if request_digest is not None and not self._remote.stage(
            evidence_digest=evidence_digest,
            request_digest=request_digest,
            revocation_epoch=revocation_epoch,
        ):
            raise SpeechPrivacyLifecycleError("speech_privacy_remote_request_failed")
        tombstone = SpeechPrivacyTombstone(
            scope_digest=scope_digest,
            evidence_digest=evidence_digest,
            phase=phase,
            revocation_epoch=revocation_epoch,
            safe_state=SAFE_STATE_BY_PHASE[phase],
            local_fenced=True,
            key_destroyed=True,
            remote_state="unresolved" if remote_required else "not_required",
            remote_request_digest=request_digest,
            remote_ack_digest=None,
        )
        return self._tombstones.put_once(tombstone)

    def acknowledge_remote(
        self,
        *,
        evidence_digest: str,
        request_digest: str,
        ack_digest: str,
        signature_verified: bool,
    ) -> SpeechPrivacyTombstone:
        for value in (evidence_digest, request_digest, ack_digest):
            _digest(value)
        row = self._tombstones.get(evidence_digest)
        if row is None:
            raise SpeechPrivacyLifecycleError("speech_privacy_tombstone_missing")
        if not signature_verified or row.remote_request_digest != request_digest:
            raise SpeechPrivacyLifecycleError("speech_privacy_remote_ack_invalid")
        if row.remote_ack_digest is not None:
            if row.remote_ack_digest != ack_digest:
                raise SpeechPrivacyLifecycleError("speech_privacy_remote_ack_conflict")
            return row
        if not self._remote.acknowledge(
            evidence_digest=evidence_digest,
            request_digest=request_digest,
            ack_digest=ack_digest,
            signature_verified=signature_verified,
        ):
            raise SpeechPrivacyLifecycleError("speech_privacy_remote_ack_failed")
        return self._tombstones.replace(replace(row, remote_state="acknowledged", remote_ack_digest=ack_digest))

    def preflight_import(self, *, evidence_digest: str) -> None:
        _digest(evidence_digest)
        if self._tombstones.get(evidence_digest) is not None:
            raise SpeechPrivacyLifecycleError("speech_privacy_reimport_revoked")


def _digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SpeechPrivacyLifecycleError("speech_privacy_digest_invalid")


__all__ = [
    "InMemorySpeechPrivacyTombstoneRepository",
    "SAFE_STATE_BY_PHASE",
    "SPEECH_DATA_PHASES",
    "SpeechPrivacyLifecycleError",
    "SpeechPrivacyLifecycleService",
    "SpeechPrivacyRemotePort",
    "SpeechPrivacyTombstone",
]
