"""Scoped, non-media SFU member digest service.

The provider deliberately exposes digests, never derived key material.  Its root
secrets are a separate key domain from media/content encryption keys and are
resolved through an injected store.  Callers must treat
``SfuMemberDigestUnavailable`` as a deny decision.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Protocol, Sequence


_DIGEST_DOMAIN = b"ananta:sfu-member-digest:v1"


class DigestKeyState(str, Enum):
    """Allowed lifecycle states for a dedicated member-digest root secret."""

    GENERATION = "generation"
    ACTIVE = "active"
    DUAL_READ = "dual_read"
    RETIRED = "retired"
    DESTROYED = "destroyed"


class SfuMemberDigestPolicyError(ValueError):
    """Raised when key policy is unsupported or internally inconsistent."""


class SfuMemberDigestUnavailable(RuntimeError):
    """Raised when no unambiguous policy-compliant signing key is available."""


@dataclass(frozen=True)
class SfuMemberDigest:
    """Portable digest metadata; it never contains secret or derived key bytes."""

    algorithm: str
    key_id: str
    scope: str
    digest: str


@dataclass(frozen=True)
class SfuMemberDigestKeyRecord:
    """Atomic secret-store result consumed only by the digest provider."""

    key_id: str
    generation: int
    algorithm: str
    scope: str
    state: DigestKeyState | str
    secret: bytes | None = field(repr=False, compare=False)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class SfuMemberDigestClock(Protocol):
    """Clock seam used for deterministic validity and rotation decisions."""

    def now(self) -> datetime:
        """Return a timezone-aware instant."""


class SfuMemberDigestSecretStore(Protocol):
    """Minimal read port; lifecycle writes remain an adapter responsibility."""

    def get(self, key_id: str) -> SfuMemberDigestKeyRecord | None:
        """Return one atomic key snapshot, or ``None`` when it is unknown."""

    def list_for_scope(self, scope: str) -> Sequence[SfuMemberDigestKeyRecord]:
        """Return atomic key snapshots assigned to the exact scope."""


@dataclass(frozen=True)
class SfuMemberDigestKeyPolicy:
    """Fail-closed cryptographic and lifecycle constraints."""

    algorithm: str = "HMAC-SHA256"
    kdf: str = "HKDF-SHA256"
    digest_bytes: int = 32
    minimum_secret_bytes: int = 32
    maximum_member_identifier_bytes: int = 1024
    maximum_active_seconds: int = 604_800
    maximum_dual_read_seconds: int = 86_400
    key_id_pattern: str = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    scope_pattern: str = r"^[A-Za-z0-9][A-Za-z0-9:/._-]{0,255}$"

    def __post_init__(self) -> None:
        if self.algorithm != "HMAC-SHA256":
            raise SfuMemberDigestPolicyError("only HMAC-SHA256 is supported")
        if self.kdf != "HKDF-SHA256":
            raise SfuMemberDigestPolicyError("only HKDF-SHA256 is supported")
        if self.digest_bytes != hashlib.sha256().digest_size:
            raise SfuMemberDigestPolicyError("SHA-256 digests must be 32 bytes")
        integer_limits = (
            self.minimum_secret_bytes,
            self.maximum_member_identifier_bytes,
            self.maximum_active_seconds,
            self.maximum_dual_read_seconds,
        )
        if any(isinstance(value, bool) or value <= 0 for value in integer_limits):
            raise SfuMemberDigestPolicyError("policy limits must be positive integers")
        if self.minimum_secret_bytes < hashlib.sha256().digest_size:
            raise SfuMemberDigestPolicyError("root secrets must provide 256 bits")
        try:
            re.compile(self.key_id_pattern, re.ASCII)
            re.compile(self.scope_pattern, re.ASCII)
        except re.error as exc:
            raise SfuMemberDigestPolicyError("invalid identifier pattern") from exc

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "SfuMemberDigestKeyPolicy":
        """Build policy from the repository JSON shape without performing I/O."""

        rotation = raw.get("rotation", {})
        limits = raw.get("limits", {})
        identifiers = raw.get("identifiers", {})
        if not all(isinstance(item, Mapping) for item in (rotation, limits, identifiers)):
            raise SfuMemberDigestPolicyError("policy sections must be objects")
        try:
            return cls(
                algorithm=str(raw["algorithm"]),
                kdf=str(raw["kdf"]),
                digest_bytes=int(raw["digest_bytes"]),
                minimum_secret_bytes=int(limits["minimum_secret_bytes"]),
                maximum_member_identifier_bytes=int(
                    limits["maximum_member_identifier_bytes"]
                ),
                maximum_active_seconds=int(rotation["maximum_active_seconds"]),
                maximum_dual_read_seconds=int(
                    rotation["maximum_dual_read_seconds"]
                ),
                key_id_pattern=str(identifiers["key_id_pattern"]),
                scope_pattern=str(identifiers["scope_pattern"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SfuMemberDigestPolicyError("incomplete or invalid key policy") from exc


class SfuMemberDigestKeyProvider:
    """Create and verify scoped member digests without exporting key material."""

    def __init__(
        self,
        *,
        secret_store: SfuMemberDigestSecretStore,
        clock: SfuMemberDigestClock,
        policy: SfuMemberDigestKeyPolicy,
    ) -> None:
        self._secret_store = secret_store
        self._clock = clock
        self._policy = policy

    def create_digest(self, *, member_identifier: str, scope: str) -> SfuMemberDigest:
        """Create a digest with the sole currently usable active key."""

        canonical_member = self._canonical_member_identifier(member_identifier)
        self._validate_scope(scope)
        now = self._now()
        try:
            records = tuple(self._secret_store.list_for_scope(scope))
        except Exception as exc:
            raise SfuMemberDigestUnavailable("secret store unavailable") from exc

        usable: list[SfuMemberDigestKeyRecord] = []
        for record in records:
            self._validate_record(record, expected_scope=scope)
            state = DigestKeyState(record.state)
            if state is DigestKeyState.ACTIVE and self._inside_window(record, now):
                usable.append(record)
        if len(usable) != 1:
            raise SfuMemberDigestUnavailable(
                "exactly one bounded active digest key is required"
            )

        record = usable[0]
        raw_digest = self._compute(record, canonical_member)
        return SfuMemberDigest(
            algorithm=self._policy.algorithm,
            key_id=record.key_id,
            scope=scope,
            digest=self._encode(raw_digest),
        )

    def verify_digest(
        self,
        *,
        member_identifier: str,
        expected_scope: str,
        candidate: SfuMemberDigest,
    ) -> bool:
        """Verify active/dual-read digests; every malformed or stale input denies."""

        try:
            canonical_member = self._canonical_member_identifier(member_identifier)
            self._validate_scope(expected_scope)
            if not isinstance(candidate, SfuMemberDigest):
                return False
            if candidate.algorithm != self._policy.algorithm:
                return False
            if candidate.scope != expected_scope:
                return False
            self._validate_key_id(candidate.key_id)
            presented = self._decode(candidate.digest)
            record = self._secret_store.get(candidate.key_id)
            if record is None:
                return False
            self._validate_record(record, expected_scope=expected_scope)
            state = DigestKeyState(record.state)
            if state not in (DigestKeyState.ACTIVE, DigestKeyState.DUAL_READ):
                return False
            if not self._inside_window(record, self._now()):
                return False
            expected = self._compute(record, canonical_member)
            return hmac.compare_digest(presented, expected)
        except Exception:
            # Verification is an authorization boundary.  Store, policy, metadata,
            # clock, and input failures all produce a deny result.
            return False

    def _validate_record(
        self, record: SfuMemberDigestKeyRecord, *, expected_scope: str
    ) -> None:
        if not isinstance(record, SfuMemberDigestKeyRecord):
            raise SfuMemberDigestUnavailable("invalid secret-store record")
        self._validate_key_id(record.key_id)
        if isinstance(record.generation, bool) or record.generation <= 0:
            raise SfuMemberDigestUnavailable("invalid key generation")
        if record.algorithm != self._policy.algorithm:
            raise SfuMemberDigestUnavailable("unsupported key algorithm")
        if record.scope != expected_scope:
            raise SfuMemberDigestUnavailable("secret-store scope mismatch")
        try:
            state = DigestKeyState(record.state)
        except ValueError as exc:
            raise SfuMemberDigestUnavailable("unknown key lifecycle state") from exc

        if state is DigestKeyState.DESTROYED:
            if record.secret is not None:
                raise SfuMemberDigestUnavailable("destroyed key retains secret material")
            return
        if not isinstance(record.secret, bytes):
            raise SfuMemberDigestUnavailable("key material is absent")
        if len(record.secret) < self._policy.minimum_secret_bytes:
            raise SfuMemberDigestUnavailable("key material is below policy strength")
        if state in (DigestKeyState.ACTIVE, DigestKeyState.DUAL_READ):
            start = self._aware_utc(record.valid_from)
            end = self._aware_utc(record.valid_until)
            if start >= end:
                raise SfuMemberDigestUnavailable("invalid key validity window")
            maximum = (
                self._policy.maximum_active_seconds
                if state is DigestKeyState.ACTIVE
                else self._policy.maximum_dual_read_seconds
            )
            if (end - start).total_seconds() > maximum:
                raise SfuMemberDigestUnavailable("key validity window exceeds policy")

    def _inside_window(self, record: SfuMemberDigestKeyRecord, now: datetime) -> bool:
        start = self._aware_utc(record.valid_from)
        end = self._aware_utc(record.valid_until)
        return start <= now < end

    def _compute(self, record: SfuMemberDigestKeyRecord, member: bytes) -> bytes:
        if record.secret is None:
            raise SfuMemberDigestUnavailable("key material is absent")
        salt = hashlib.sha256(
            _DIGEST_DOMAIN
            + self._frame(record.algorithm.encode("ascii"))
            + self._frame(record.key_id.encode("ascii"))
            + self._frame(str(record.generation).encode("ascii"))
        ).digest()
        pseudorandom_key = hmac.new(salt, record.secret, hashlib.sha256).digest()
        info = _DIGEST_DOMAIN + self._frame(record.scope.encode("ascii"))
        derived_key = hmac.new(
            pseudorandom_key, info + b"\x01", hashlib.sha256
        ).digest()[: self._policy.digest_bytes]
        message = _DIGEST_DOMAIN + self._frame(record.scope.encode("ascii"))
        message += self._frame(member)
        return hmac.new(derived_key, message, hashlib.sha256).digest()

    def _canonical_member_identifier(self, value: str) -> bytes:
        if not isinstance(value, str):
            raise SfuMemberDigestUnavailable("member identifier must be text")
        canonical = unicodedata.normalize("NFC", value)
        encoded = canonical.encode("utf-8")
        if not encoded or b"\x00" in encoded:
            raise SfuMemberDigestUnavailable("member identifier is empty or invalid")
        if len(encoded) > self._policy.maximum_member_identifier_bytes:
            raise SfuMemberDigestUnavailable("member identifier exceeds policy bound")
        return encoded

    def _validate_key_id(self, value: str) -> None:
        if not isinstance(value, str) or re.fullmatch(
            self._policy.key_id_pattern, value, re.ASCII
        ) is None:
            raise SfuMemberDigestUnavailable("invalid digest key id")

    def _validate_scope(self, value: str) -> None:
        if not isinstance(value, str) or re.fullmatch(
            self._policy.scope_pattern, value, re.ASCII
        ) is None:
            raise SfuMemberDigestUnavailable("invalid digest scope")

    def _now(self) -> datetime:
        return self._aware_utc(self._clock.now())

    @staticmethod
    def _aware_utc(value: datetime | None) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise SfuMemberDigestUnavailable("timezone-aware timestamp required")
        if value.utcoffset() is None:
            raise SfuMemberDigestUnavailable("timezone-aware timestamp required")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _frame(value: bytes) -> bytes:
        return len(value).to_bytes(4, "big") + value

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def _decode(self, value: str) -> bytes:
        if not isinstance(value, str) or not value or "=" in value:
            raise SfuMemberDigestUnavailable("invalid digest encoding")
        try:
            decoded = base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, UnicodeEncodeError) as exc:
            raise SfuMemberDigestUnavailable("invalid digest encoding") from exc
        if len(decoded) != self._policy.digest_bytes or self._encode(decoded) != value:
            raise SfuMemberDigestUnavailable("non-canonical digest encoding")
        return decoded
