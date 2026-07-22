"""Canonical non-media digest key contract for SFU audience identities.

The service depends on metadata and cryptographic ports.  The crypto port
accepts a key reference and never returns raw key material, which keeps KMS or
wrapped-key ownership outside the Hub service process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol, Sequence


class DigestKeyLifecycleState(str, Enum):
    STAGED = "staged"
    ACTIVE = "active"
    DUAL_READ = "dual_read"
    RETIRED = "retired"
    DESTRUCTION_PENDING = "destruction_pending"
    DESTROYED = "destroyed"


class SfuMemberDigestReason(str, Enum):
    SCOPE_INVALID = "member_digest_scope_invalid"
    PAYLOAD_OVERSIZE = "member_digest_payload_oversize"
    KEY_UNAVAILABLE = "member_digest_key_unavailable"
    KEY_AMBIGUOUS = "member_digest_key_ambiguous"
    KEY_SCOPE_MISMATCH = "member_digest_key_scope_mismatch"
    KEY_STATE_INVALID = "member_digest_key_state_invalid"
    KEY_EXPIRED = "member_digest_key_expired"
    KEY_VERSION_CONFLICT = "member_digest_key_version_conflict"
    ALGORITHM_MISMATCH = "member_digest_algorithm_mismatch"
    ROTATION_OVERLAP_EXCEEDED = "member_digest_rotation_overlap_exceeded"
    RETENTION_EXCEEDED = "member_digest_retention_exceeded"
    DIGEST_INVALID = "member_digest_invalid"
    KMS_FAILURE = "member_digest_kms_failure"


class SfuMemberDigestContractError(RuntimeError):
    def __init__(self, reason_code: SfuMemberDigestReason, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


_SCOPE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class SfuMemberDigestScope:
    tenant_id: str
    room_id: str
    publication_id: str
    key_epoch: int

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.room_id, self.publication_id):
            if not _SCOPE_COMPONENT.fullmatch(value):
                raise SfuMemberDigestContractError(
                    SfuMemberDigestReason.SCOPE_INVALID,
                    "digest scope components must be bounded opaque identifiers",
                )
        if not 0 <= self.key_epoch <= 9_007_199_254_740_991:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.SCOPE_INVALID,
                "key_epoch is outside the cross-runtime safe range",
            )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "key_epoch": self.key_epoch,
                "publication_id": self.publication_id,
                "room_id": self.room_id,
                "tenant_id": self.tenant_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class DigestKeyMetadata:
    key_id: str
    algorithm: str
    generation: int
    version: int
    scope_fingerprint: str
    state: DigestKeyLifecycleState
    valid_from: datetime
    valid_until: datetime
    state_changed_at: datetime
    dual_read_until: datetime | None = None
    retain_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class SfuMemberDigestValue:
    contract_version: int
    algorithm: str
    key_id: str
    generation: int
    key_version: int
    scope_fingerprint: str
    value: str


@dataclass(frozen=True, slots=True)
class DigestVerificationResult:
    valid: bool
    reason_code: SfuMemberDigestReason | None = None


@dataclass(frozen=True, slots=True)
class DigestKeyContractPolicy:
    algorithm: str = "HMAC-SHA256"
    digest_bytes: int = 32
    maximum_payload_bytes: int = 1_024
    maximum_active_lifetime: timedelta = timedelta(days=7)
    maximum_dual_read_overlap: timedelta = timedelta(days=1)
    maximum_retention: timedelta = timedelta(days=30)
    maximum_keys_per_scope: int = 16

    def __post_init__(self) -> None:
        if self.algorithm != "HMAC-SHA256" or self.digest_bytes != 32:
            raise ValueError("only the versioned HMAC-SHA256 digest contract is supported")
        if self.maximum_payload_bytes < 1 or self.maximum_keys_per_scope < 2:
            raise ValueError("digest policy bounds are invalid")
        if min(
            self.maximum_active_lifetime.total_seconds(),
            self.maximum_dual_read_overlap.total_seconds(),
            self.maximum_retention.total_seconds(),
        ) < 0:
            raise ValueError("digest lifecycle durations cannot be negative")


class DigestClock(Protocol):
    def now(self) -> datetime: ...


class DigestKeyMetadataReader(Protocol):
    def get(self, key_id: str) -> DigestKeyMetadata | None: ...

    def list_for_scope(self, scope_fingerprint: str) -> Sequence[DigestKeyMetadata]: ...


@dataclass(frozen=True, slots=True)
class DigestKeyRotationRequest:
    current_key_id: str
    expected_current_version: int
    successor: DigestKeyMetadata
    transitioned_at: datetime
    dual_read_until: datetime
    retain_until: datetime
    compromised: bool


class DigestKeyLifecycleWriter(Protocol):
    def rotate(
        self,
        request: DigestKeyRotationRequest,
    ) -> tuple[DigestKeyMetadata, DigestKeyMetadata]: ...


class DigestKeyCryptoPort(Protocol):
    """KMS-facing port; implementations must not return raw key bytes."""

    def mac_sha256(self, key_id: str, message: bytes) -> bytes: ...

    def destroy(self, key_id: str) -> None: ...


class SystemDigestClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SfuMemberDigestKeyContractService:
    _DOMAIN = b"ananta.webrtc.receiver-group.member-digest.v2\x00"

    def __init__(
        self,
        *,
        reader: DigestKeyMetadataReader,
        writer: DigestKeyLifecycleWriter,
        crypto: DigestKeyCryptoPort,
        clock: DigestClock | None = None,
        policy: DigestKeyContractPolicy | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._crypto = crypto
        self._clock = clock or SystemDigestClock()
        self._policy = policy or DigestKeyContractPolicy()

    def create_digest(
        self,
        payload: bytes,
        scope: SfuMemberDigestScope,
    ) -> SfuMemberDigestValue:
        self._validate_payload(payload)
        now = self._aware_now()
        candidates = [
            record
            for record in self._reader.list_for_scope(scope.fingerprint())
            if record.state is DigestKeyLifecycleState.ACTIVE
            and record.valid_from <= now < record.valid_until
        ]
        if not candidates:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_UNAVAILABLE,
                "no active digest key is available for the exact scope",
            )
        if len(candidates) != 1:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_AMBIGUOUS,
                "multiple active digest keys exist for the exact scope",
            )
        key = candidates[0]
        self._validate_metadata(key, scope)
        raw_digest = self._compute(key.key_id, self._message(payload, scope))
        return SfuMemberDigestValue(
            contract_version=1,
            algorithm=key.algorithm,
            key_id=key.key_id,
            generation=key.generation,
            key_version=key.version,
            scope_fingerprint=key.scope_fingerprint,
            value=base64.urlsafe_b64encode(raw_digest).rstrip(b"=").decode("ascii"),
        )

    def verify_digest(
        self,
        payload: bytes,
        scope: SfuMemberDigestScope,
        candidate: SfuMemberDigestValue,
    ) -> DigestVerificationResult:
        try:
            self._validate_payload(payload)
            if candidate.contract_version != 1:
                return DigestVerificationResult(
                    False,
                    SfuMemberDigestReason.DIGEST_INVALID,
                )
            key = self._reader.get(candidate.key_id)
            if key is None:
                return DigestVerificationResult(
                    False,
                    SfuMemberDigestReason.KEY_UNAVAILABLE,
                )
            self._validate_metadata(key, scope)
            if (
                candidate.algorithm != key.algorithm
                or candidate.generation != key.generation
                or candidate.scope_fingerprint != key.scope_fingerprint
            ):
                return DigestVerificationResult(
                    False,
                    SfuMemberDigestReason.DIGEST_INVALID,
                )
            now = self._aware_now()
            if key.state is DigestKeyLifecycleState.ACTIVE:
                permitted = (
                    key.valid_from <= now < key.valid_until
                    and candidate.key_version == key.version
                )
            elif key.state is DigestKeyLifecycleState.DUAL_READ:
                permitted = (
                    key.dual_read_until is not None
                    and max(key.valid_from, key.state_changed_at) <= now
                    and now < key.valid_until
                    and now < key.dual_read_until
                    and candidate.key_version in {key.version, key.version - 1}
                )
            else:
                permitted = False
            if not permitted:
                return DigestVerificationResult(
                    False,
                    SfuMemberDigestReason.KEY_STATE_INVALID,
                )
            supplied = self._decode_digest(candidate.value)
            expected = self._compute(key.key_id, self._message(payload, scope))
            if not hmac.compare_digest(supplied, expected):
                return DigestVerificationResult(
                    False,
                    SfuMemberDigestReason.DIGEST_INVALID,
                )
            return DigestVerificationResult(True)
        except SfuMemberDigestContractError as exc:
            return DigestVerificationResult(False, exc.reason_code)

    def rotate(
        self,
        *,
        scope: SfuMemberDigestScope,
        current_key_id: str,
        expected_current_version: int,
        successor: DigestKeyMetadata,
        overlap: timedelta,
        retention: timedelta,
        compromised: bool = False,
    ) -> tuple[DigestKeyMetadata, DigestKeyMetadata]:
        now = self._aware_now()
        current = self._reader.get(current_key_id)
        if current is None:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_UNAVAILABLE,
                "the current digest key does not exist",
            )
        if current.version != expected_current_version:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                "the digest key lifecycle version changed",
            )
        self._validate_metadata(current, scope)
        self._validate_metadata(successor, scope, allow_staged=True)
        if successor.state is not DigestKeyLifecycleState.STAGED:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_STATE_INVALID,
                "the successor digest key must be staged",
            )
        if successor.generation <= current.generation:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                "digest key generations must increase monotonically",
            )
        if successor.valid_until - successor.valid_from > self._policy.maximum_active_lifetime:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_EXPIRED,
                "successor active lifetime exceeds policy",
            )
        effective_overlap = timedelta(0) if compromised else overlap
        if effective_overlap < timedelta(0) or effective_overlap > self._policy.maximum_dual_read_overlap:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.ROTATION_OVERLAP_EXCEEDED,
                "dual-read overlap exceeds policy",
            )
        if not compromised and now + effective_overlap > current.valid_until:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.ROTATION_OVERLAP_EXCEEDED,
                "dual-read overlap exceeds the current key validity window",
            )
        if retention < timedelta(0) or retention > self._policy.maximum_retention:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.RETENTION_EXCEEDED,
                "digest key retention exceeds policy",
            )
        scope_records = self._reader.list_for_scope(scope.fingerprint())
        if len(scope_records) > self._policy.maximum_keys_per_scope:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_STATE_INVALID,
                "digest key scope exceeds the configured metadata bound",
            )
        old_record, new_record = self._writer.rotate(
            DigestKeyRotationRequest(
                current_key_id=current_key_id,
                expected_current_version=expected_current_version,
                successor=successor,
                transitioned_at=now,
                dual_read_until=now + effective_overlap,
                retain_until=now + retention,
                compromised=compromised,
            )
        )
        if compromised:
            try:
                self._crypto.destroy(current_key_id)
            except Exception as exc:
                # Metadata is already fail-closed. Returning the explicit pending
                # state avoids claiming that the committed rotation failed and
                # lets an idempotent reconciler retry physical destruction.
                return old_record, new_record
            old_record = self._writer.destroy(
                current_key_id,
                expected_version=old_record.version,
                transitioned_at=now,
                retain_until=old_record.retain_until or now,
            )
        return old_record, new_record

    def complete_pending_destruction(
        self,
        *,
        scope: SfuMemberDigestScope,
        key_id: str,
        expected_version: int,
    ) -> DigestKeyMetadata:
        """Idempotently complete a committed compromised-key destruction."""
        record = self._reader.get(key_id)
        if record is None:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_UNAVAILABLE,
                "pending digest key metadata is unavailable",
            )
        self._validate_metadata(record, scope)
        if record.state is DigestKeyLifecycleState.DESTROYED:
            return record
        if (
            record.state is not DigestKeyLifecycleState.DESTRUCTION_PENDING
            or record.version != expected_version
        ):
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                "pending digest key destruction compare-and-swap failed",
            )
        try:
            self._crypto.destroy(key_id)
        except Exception as exc:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KMS_FAILURE,
                "pending digest key destruction failed",
            ) from exc
        now = self._aware_now()
        return self._writer.destroy(
            key_id,
            expected_version=record.version,
            transitioned_at=now,
            retain_until=record.retain_until or now,
        )

    def _validate_metadata(
        self,
        record: DigestKeyMetadata,
        scope: SfuMemberDigestScope,
        *,
        allow_staged: bool = False,
    ) -> None:
        if record.algorithm != self._policy.algorithm:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.ALGORITHM_MISMATCH,
                "digest key algorithm is not allowed",
            )
        if record.scope_fingerprint != scope.fingerprint():
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_SCOPE_MISMATCH,
                "digest key scope does not match the requested scope",
            )
        if record.generation < 1 or record.version < 1:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_VERSION_CONFLICT,
                "digest key generation and version must be positive",
            )
        if (
            record.valid_from.tzinfo is None
            or record.valid_until.tzinfo is None
            or record.state_changed_at.tzinfo is None
            or (record.dual_read_until is not None and record.dual_read_until.tzinfo is None)
            or (record.retain_until is not None and record.retain_until.tzinfo is None)
        ):
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_EXPIRED,
                "digest key timestamps must be timezone-aware",
            )
        if record.valid_until <= record.valid_from:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_EXPIRED,
                "digest key validity window is invalid",
            )
        if (
            record.dual_read_until is not None
            and (
                record.dual_read_until <= record.state_changed_at
                or record.dual_read_until > record.valid_until
            )
        ):
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_EXPIRED,
                "digest key dual-read window is invalid",
            )
        if record.retain_until is not None and record.retain_until < record.state_changed_at:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.RETENTION_EXCEEDED,
                "digest key retention window is invalid",
            )
        if not allow_staged and record.state is DigestKeyLifecycleState.STAGED:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KEY_STATE_INVALID,
                "staged keys cannot serve digest operations",
            )

    def _validate_payload(self, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("member digest payload must be bytes")
        if len(payload) > self._policy.maximum_payload_bytes:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.PAYLOAD_OVERSIZE,
                "member digest payload exceeds policy",
            )

    def _compute(self, key_id: str, message: bytes) -> bytes:
        try:
            digest = self._crypto.mac_sha256(key_id, message)
        except SfuMemberDigestContractError:
            raise
        except Exception as exc:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KMS_FAILURE,
                "digest KMS operation failed",
            ) from exc
        if len(digest) != self._policy.digest_bytes:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.KMS_FAILURE,
                "digest KMS returned an invalid digest length",
            )
        return digest

    def _message(self, payload: bytes, scope: SfuMemberDigestScope) -> bytes:
        scope_bytes = scope.canonical_bytes()
        return b"".join(
            (
                self._DOMAIN,
                len(scope_bytes).to_bytes(4, "big"),
                scope_bytes,
                len(payload).to_bytes(4, "big"),
                payload,
            )
        )

    def _decode_digest(self, value: str) -> bytes:
        try:
            padding = "=" * (-len(value) % 4)
            decoded = base64.b64decode(
                value + padding,
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, TypeError) as exc:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.DIGEST_INVALID,
                "member digest encoding is invalid",
            ) from exc
        if len(decoded) != self._policy.digest_bytes:
            raise SfuMemberDigestContractError(
                SfuMemberDigestReason.DIGEST_INVALID,
                "member digest length is invalid",
            )
        return decoded

    def _aware_now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None:
            raise ValueError("digest clock must return a timezone-aware timestamp")
        return now
