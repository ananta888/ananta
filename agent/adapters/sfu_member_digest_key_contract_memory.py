"""Explicit development/test adapters for the digest key contract ports.

Production wiring must replace both adapters with a durable metadata
repository and a KMS/wrapped-key implementation.  Shared state is injectable so
tests can exercise multi-Hub and restart behavior without coupling the service
to process-local globals.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime

from agent.services.sfu_member_digest_key_contract import (
    DigestKeyLifecycleState,
    DigestKeyMetadata,
    DigestKeyRotationRequest,
    SfuMemberDigestContractError,
    SfuMemberDigestReason,
)


@dataclass(slots=True)
class InMemoryDigestMetadataState:
    records: dict[str, DigestKeyMetadata] = field(default_factory=dict, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class InMemoryDigestKeyMetadataRepository:
    def __init__(self, state: InMemoryDigestMetadataState | None = None) -> None:
        self._state = state or InMemoryDigestMetadataState()

    def get(self, key_id: str) -> DigestKeyMetadata | None:
        with self._state.lock:
            return self._state.records.get(key_id)

    def list_for_scope(self, scope_fingerprint: str) -> tuple[DigestKeyMetadata, ...]:
        with self._state.lock:
            return tuple(
                sorted(
                    (
                        record
                        for record in self._state.records.values()
                        if record.scope_fingerprint == scope_fingerprint
                    ),
                    key=lambda record: (record.generation, record.key_id),
                )
            )

    def stage(self, record: DigestKeyMetadata) -> DigestKeyMetadata:
        if record.state is not DigestKeyLifecycleState.STAGED:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_STATE_INVALID,
                "new digest keys must enter the repository as staged",
            )
        with self._state.lock:
            if record.key_id in self._state.records:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                    "digest key id already exists",
                )
            same_scope = [
                candidate
                for candidate in self._state.records.values()
                if candidate.scope_fingerprint == record.scope_fingerprint
            ]
            if same_scope and record.generation <= max(
                candidate.generation for candidate in same_scope
            ):
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                    "digest key generations must increase monotonically",
                )
            if len(same_scope) >= 16:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_STATE_INVALID,
                    "digest key scope exceeds the bounded metadata set",
                )
            self._state.records[record.key_id] = record
            return record

    def activate(
        self,
        key_id: str,
        *,
        expected_version: int,
        transitioned_at: datetime,
    ) -> DigestKeyMetadata:
        with self._state.lock:
            record = self._require_version(key_id, expected_version)
            if record.state is not DigestKeyLifecycleState.STAGED:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_STATE_INVALID,
                    "only staged digest keys can be activated",
                )
            active = [
                candidate
                for candidate in self._state.records.values()
                if candidate.scope_fingerprint == record.scope_fingerprint
                and candidate.state is DigestKeyLifecycleState.ACTIVE
            ]
            if active:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_AMBIGUOUS,
                    "an active digest key already exists for this scope",
                )
            activated = replace(
                record,
                state=DigestKeyLifecycleState.ACTIVE,
                version=record.version + 1,
                state_changed_at=transitioned_at,
            )
            self._state.records[key_id] = activated
            return activated

    def rotate(
        self,
        request: DigestKeyRotationRequest,
    ) -> tuple[DigestKeyMetadata, DigestKeyMetadata]:
        with self._state.lock:
            current = self._require_version(
                request.current_key_id,
                request.expected_current_version,
            )
            if current.state is not DigestKeyLifecycleState.ACTIVE:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_STATE_INVALID,
                    "only the active digest key can be rotated",
                )
            staged = self._state.records.get(request.successor.key_id)
            if staged is None or staged != request.successor:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                    "the staged successor changed before rotation",
                )
            if staged.state is not DigestKeyLifecycleState.STAGED:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_STATE_INVALID,
                    "the rotation successor is not staged",
                )
            if (
                staged.scope_fingerprint != current.scope_fingerprint
                or staged.generation <= current.generation
            ):
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_SCOPE_MISMATCH,
                    "rotation successor scope or generation is invalid",
                )

            previous = replace(
                current,
                state=(
                    DigestKeyLifecycleState.DESTRUCTION_PENDING
                    if request.compromised
                    else DigestKeyLifecycleState.DUAL_READ
                ),
                version=current.version + 1,
                state_changed_at=request.transitioned_at,
                dual_read_until=(
                    None if request.compromised else request.dual_read_until
                ),
                retain_until=request.retain_until,
            )
            successor = replace(
                staged,
                state=DigestKeyLifecycleState.ACTIVE,
                version=staged.version + 1,
                state_changed_at=request.transitioned_at,
            )
            self._state.records[current.key_id] = previous
            self._state.records[staged.key_id] = successor
            return previous, successor

    def retire(
        self,
        key_id: str,
        *,
        expected_version: int,
        transitioned_at: datetime,
        retain_until: datetime,
    ) -> DigestKeyMetadata:
        with self._state.lock:
            record = self._require_version(key_id, expected_version)
            if record.state is not DigestKeyLifecycleState.DUAL_READ:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_STATE_INVALID,
                    "only a dual-read digest key can be retired",
                )
            retired = replace(
                record,
                state=DigestKeyLifecycleState.RETIRED,
                version=record.version + 1,
                state_changed_at=transitioned_at,
                dual_read_until=None,
                retain_until=retain_until,
            )
            self._state.records[key_id] = retired
            return retired

    def destroy(
        self,
        key_id: str,
        *,
        expected_version: int,
        transitioned_at: datetime,
        retain_until: datetime,
    ) -> DigestKeyMetadata:
        with self._state.lock:
            record = self._require_version(key_id, expected_version)
            destroyed = replace(
                record,
                state=DigestKeyLifecycleState.DESTROYED,
                version=record.version + 1,
                state_changed_at=transitioned_at,
                dual_read_until=None,
                retain_until=retain_until,
            )
            self._state.records[key_id] = destroyed
            return destroyed

    def purge_expired_metadata(self, now: datetime) -> int:
        with self._state.lock:
            purge_ids = [
                record.key_id
                for record in self._state.records.values()
                if record.state is DigestKeyLifecycleState.DESTROYED
                and record.retain_until is not None
                and record.retain_until <= now
            ]
            for key_id in purge_ids:
                del self._state.records[key_id]
            return len(purge_ids)

    def _require_version(self, key_id: str, expected_version: int) -> DigestKeyMetadata:
        record = self._state.records.get(key_id)
        if record is None:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_UNAVAILABLE,
                "digest key metadata is unavailable",
            )
        if record.version != expected_version:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                "digest key metadata compare-and-swap failed",
            )
        return record


@dataclass(slots=True)
class InMemoryDigestCryptoState:
    secrets: dict[str, bytes] = field(default_factory=dict, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class InMemoryDigestKeyCryptoAdapter:
    """Test-only KMS stand-in; production code must inject a real crypto port."""

    def __init__(self, state: InMemoryDigestCryptoState | None = None) -> None:
        self._state = state or InMemoryDigestCryptoState()

    def provision(self, key_id: str, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("test digest secrets must contain at least 32 bytes")
        with self._state.lock:
            if key_id in self._state.secrets:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                    "digest KMS key id already exists",
                )
            self._state.secrets[key_id] = bytes(secret)

    def mac_sha256(self, key_id: str, message: bytes) -> bytes:
        with self._state.lock:
            secret = self._state.secrets.get(key_id)
            if secret is None:
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.KEY_UNAVAILABLE,
                    "digest KMS key is unavailable",
                )
            return hmac.new(secret, message, hashlib.sha256).digest()

    def destroy(self, key_id: str) -> None:
        with self._state.lock:
            self._state.secrets.pop(key_id, None)
