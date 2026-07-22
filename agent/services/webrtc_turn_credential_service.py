"""Hub-owned issuance and validation of short-lived TURN credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field, replace as dataclass_replace
from threading import RLock
from typing import Callable, Mapping, Protocol


class WebrtcTurnCredentialError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnCredentialPolicy:
    credential_binding_mode: str
    bearer_ttl_seconds_max: int
    allocation_lifetime_seconds_max: int
    permission_lifetime_seconds_max: int
    channel_binding_lifetime_seconds_max: int
    credential_refresh_count_max: int
    refresh_before_seconds: int
    overlap_seconds: int
    retry_max: int

    def __post_init__(self) -> None:
        if self.credential_binding_mode not in {"authorization_hook", "rest_hmac_bearer"}:
            raise WebrtcTurnCredentialError("turn_credential_binding_mode_invalid", 503)
        values = (
            self.bearer_ttl_seconds_max,
            self.allocation_lifetime_seconds_max,
            self.permission_lifetime_seconds_max,
            self.channel_binding_lifetime_seconds_max,
            self.credential_refresh_count_max,
            self.refresh_before_seconds,
            self.retry_max,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise WebrtcTurnCredentialError("turn_credential_policy_invalid", 503)
        if not isinstance(self.overlap_seconds, int) or isinstance(self.overlap_seconds, bool) or self.overlap_seconds < 0:
            raise WebrtcTurnCredentialError("turn_credential_policy_invalid", 503)
        if self.refresh_before_seconds >= self.bearer_ttl_seconds_max or self.overlap_seconds > self.refresh_before_seconds:
            raise WebrtcTurnCredentialError("turn_credential_rotation_policy_invalid", 503)

    @property
    def max_bearer_exposure_seconds(self) -> int:
        return max(
            self.bearer_ttl_seconds_max,
            self.allocation_lifetime_seconds_max,
            self.permission_lifetime_seconds_max,
            self.channel_binding_lifetime_seconds_max,
        )


@dataclass(frozen=True, slots=True)
class TurnCredentialRequest:
    tenant_ref: str
    room_ref: str
    participant_ref: str
    device_ref: str
    region: str
    pool_id: str
    admission_epoch: int
    requested_ttl_seconds: int
    refresh_count: int = 0


@dataclass(frozen=True, slots=True)
class TurnAdmissionDecision:
    active: bool
    quota_reserved: bool
    expires_at_seconds: int
    reason_code: str


class TurnAdmissionAuthorityPort(Protocol):
    def authorize(self, request: TurnCredentialRequest) -> TurnAdmissionDecision: ...


@dataclass(frozen=True, slots=True)
class TurnSigningKey:
    key_id: str
    secret: bytes = field(repr=False)


class TurnSigningKeyPort(Protocol):
    def active(self) -> TurnSigningKey: ...

    def resolve(self, key_id: str) -> TurnSigningKey | None: ...


class InMemoryTurnSigningKeyRing:
    """Bounded key ring for tests; production keys must come from a secret manager."""

    def __init__(self, key: TurnSigningKey, *, max_keys: int = 2) -> None:
        self._validate(key)
        if max_keys < 1:
            raise ValueError("turn_signing_key_ring_limit_invalid")
        self._keys = {key.key_id: key}
        self._active = key.key_id
        self._max_keys = max_keys
        self._lock = RLock()

    def active(self) -> TurnSigningKey:
        with self._lock:
            return self._keys[self._active]

    def resolve(self, key_id: str) -> TurnSigningKey | None:
        with self._lock:
            return self._keys.get(key_id)

    def rotate(self, key: TurnSigningKey) -> None:
        self._validate(key)
        with self._lock:
            self._keys[key.key_id] = key
            self._active = key.key_id
            while len(self._keys) > self._max_keys:
                oldest = next(key_id for key_id in self._keys if key_id != self._active)
                del self._keys[oldest]

    def emergency_rotate(self, key: TurnSigningKey) -> None:
        self._validate(key)
        with self._lock:
            self._keys = {key.key_id: key}
            self._active = key.key_id

    @staticmethod
    def _validate(key: TurnSigningKey) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key.key_id) or len(key.secret) < 32:
            raise ValueError("turn_signing_key_invalid")


@dataclass(frozen=True, slots=True)
class TurnCredentialState:
    credential_id: str
    key_id: str
    binding_mode: str
    expires_at_seconds: int
    admission_epoch: int
    refresh_count: int
    claim_digests: Mapping[str, str]
    revoked: bool = False
    version: int = 1
    rotated_to_id: str | None = None
    overlap_until_seconds: int | None = None


class TurnCredentialStatePort(Protocol):
    def put(self, state: TurnCredentialState) -> None: ...

    def get(self, credential_id: str) -> TurnCredentialState | None: ...

    def revoke(self, credential_id: str) -> bool: ...

    def rotate(
        self,
        credential_id: str,
        successor_id: str,
        *,
        expected_version: int,
        expected_claim_digests: Mapping[str, str],
        expected_admission_epoch: int,
        overlap_until_seconds: int,
    ) -> bool: ...


class InMemoryTurnCredentialStatePort:
    """Fail-closed bounded state; a Hub restart invalidates hook credentials."""

    def __init__(self, *, max_entries: int = 4096, clock: Callable[[], float] = time.time) -> None:
        if max_entries <= 0:
            raise ValueError("turn_credential_state_limit_invalid")
        self._max_entries = max_entries
        self._clock = clock
        self._states: dict[str, TurnCredentialState] = {}
        self._lock = RLock()

    def put(self, state: TurnCredentialState) -> None:
        with self._lock:
            self._purge()
            if state.credential_id not in self._states and len(self._states) >= self._max_entries:
                raise WebrtcTurnCredentialError("turn_credential_state_capacity_exceeded", 503)
            self._states[state.credential_id] = state

    def get(self, credential_id: str) -> TurnCredentialState | None:
        with self._lock:
            self._purge()
            return self._states.get(credential_id)

    def revoke(self, credential_id: str) -> bool:
        with self._lock:
            state = self._states.get(credential_id)
            if state is None:
                return False
            self._states[credential_id] = dataclass_replace(
                state, revoked=True, version=state.version + 1
            )
            return True

    def rotate(
        self,
        credential_id: str,
        successor_id: str,
        *,
        expected_version: int,
        expected_claim_digests: Mapping[str, str],
        expected_admission_epoch: int,
        overlap_until_seconds: int,
    ) -> bool:
        with self._lock:
            self._purge()
            state = self._states.get(credential_id)
            successor = self._states.get(successor_id)
            if (
                state is None
                or successor is None
                or state.revoked
                or state.rotated_to_id is not None
                or state.version != expected_version
                or state.claim_digests != expected_claim_digests
                or state.admission_epoch != expected_admission_epoch
                or successor.admission_epoch != expected_admission_epoch
                or successor.refresh_count != state.refresh_count + 1
                or overlap_until_seconds > state.expires_at_seconds
            ):
                return False
            self._states[credential_id] = dataclass_replace(
                state,
                version=state.version + 1,
                rotated_to_id=successor_id,
                overlap_until_seconds=overlap_until_seconds,
            )
            return True

    def _purge(self) -> None:
        now = self._clock()
        for credential_id in [key for key, value in self._states.items() if value.expires_at_seconds <= now]:
            del self._states[credential_id]


@dataclass(frozen=True, slots=True)
class TurnCredentialBundle:
    credential_id: str
    username: str = field(repr=False)
    credential: str = field(repr=False)
    credential_binding_mode: str
    key_id: str
    expires_at_seconds: int
    refresh_at_seconds: int
    refresh_count: int
    max_bearer_exposure_seconds: int
    claims_binding: Mapping[str, bool]
    active_allocation_revocable: bool


@dataclass(frozen=True, slots=True)
class TurnRevocationResult:
    revoked: bool
    terminate_active_allocation: bool
    remaining_exposure_seconds: int
    reason_code: str


class WebrtcTurnCredentialService:
    def __init__(
        self,
        *,
        policy: TurnCredentialPolicy,
        admission: TurnAdmissionAuthorityPort,
        keys: TurnSigningKeyPort,
        state: TurnCredentialStatePort,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(18),
    ) -> None:
        self._policy = policy
        self._admission = admission
        self._keys = keys
        self._state = state
        self._clock = clock
        self._token_factory = token_factory

    def issue(self, request: TurnCredentialRequest) -> TurnCredentialBundle:
        self._validate_request(request)
        decision = self._admission.authorize(request)
        now = int(self._clock())
        if not decision.active or not decision.quota_reserved:
            raise WebrtcTurnCredentialError(self._admission_reason(decision.reason_code), 403)
        ttl = min(request.requested_ttl_seconds, self._policy.bearer_ttl_seconds_max, decision.expires_at_seconds - now)
        if ttl <= self._policy.refresh_before_seconds:
            raise WebrtcTurnCredentialError("turn_credential_admission_expiring", 409)
        credential_id = self._token_factory()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", credential_id):
            raise WebrtcTurnCredentialError("turn_credential_id_generation_failed", 503)
        key = self._keys.active()
        expires_at = now + ttl
        claims = self._claim_digests(request, key.secret)
        mode = self._policy.credential_binding_mode
        username = (
            f"hook:{key.key_id}:{credential_id}:{expires_at}"
            if mode == "authorization_hook"
            else f"{expires_at}:{credential_id}:{key.key_id}"
        )
        credential = self._sign_credential(key.secret, username, claims if mode == "authorization_hook" else {})
        self._state.put(
            TurnCredentialState(
                credential_id,
                key.key_id,
                mode,
                expires_at,
                request.admission_epoch,
                request.refresh_count,
                claims,
            )
        )
        hook = mode == "authorization_hook"
        return TurnCredentialBundle(
            credential_id,
            username,
            credential,
            mode,
            key.key_id,
            expires_at,
            expires_at - self._policy.refresh_before_seconds,
            request.refresh_count,
            self._policy.max_bearer_exposure_seconds,
            {name: hook for name in ("tenant", "room", "participant", "device", "credential_id")},
            hook,
        )

    def refresh(self, credential_id: str, request: TurnCredentialRequest) -> TurnCredentialBundle:
        self._validate_request(request)
        previous = self._state.get(credential_id)
        now = int(self._clock())
        if (
            previous is None
            or previous.revoked
            or previous.rotated_to_id is not None
            or previous.expires_at_seconds <= now
        ):
            raise WebrtcTurnCredentialError("turn_credential_refresh_inactive", 409)
        if request.refresh_count != previous.refresh_count + 1 or request.refresh_count > self._policy.credential_refresh_count_max:
            raise WebrtcTurnCredentialError("turn_credential_refresh_cap_exceeded", 429)
        if now < previous.expires_at_seconds - self._policy.refresh_before_seconds:
            raise WebrtcTurnCredentialError("turn_credential_refresh_too_early", 409)
        previous_key = self._keys.resolve(previous.key_id)
        if (
            previous_key is None
            or request.admission_epoch != previous.admission_epoch
            or previous.claim_digests != self._claim_digests(request, previous_key.secret)
        ):
            raise WebrtcTurnCredentialError("turn_credential_refresh_scope_mismatch", 403)
        successor = self.issue(request)
        overlap_until = min(
            previous.expires_at_seconds,
            now + self._policy.overlap_seconds,
        )
        rotate = getattr(self._state, "rotate", None)
        rotated = bool(
            callable(rotate)
            and rotate(
                credential_id,
                successor.credential_id,
                expected_version=previous.version,
                expected_claim_digests=previous.claim_digests,
                expected_admission_epoch=previous.admission_epoch,
                overlap_until_seconds=overlap_until,
            )
        )
        if not rotated:
            self._state.revoke(successor.credential_id)
            raise WebrtcTurnCredentialError("turn_credential_refresh_conflict", 409)
        return successor

    def validate_authorization_hook(
        self,
        bundle: TurnCredentialBundle,
        request: TurnCredentialRequest,
    ) -> bool:
        if self._policy.credential_binding_mode != "authorization_hook":
            raise WebrtcTurnCredentialError("turn_authorization_hook_mode_required", 409)
        state = self._state.get(bundle.credential_id)
        now = int(self._clock())
        if (
            state is None
            or state.revoked
            or state.expires_at_seconds <= now
            or state.key_id != bundle.key_id
            or (
                state.rotated_to_id is not None
                and (
                    state.overlap_until_seconds is None
                    or state.overlap_until_seconds <= now
                )
            )
        ):
            return False
        key = self._keys.resolve(state.key_id)
        if key is None or state.claim_digests != self._claim_digests(request, key.secret):
            return False
        decision = self._admission.authorize(request)
        expected = self._sign_credential(key.secret, bundle.username, state.claim_digests)
        return (
            decision.active
            and decision.quota_reserved
            and request.admission_epoch == state.admission_epoch
            and hmac.compare_digest(bundle.credential, expected)
        )

    def revoke(self, credential_id: str) -> TurnRevocationResult:
        state = self._state.get(credential_id)
        now = int(self._clock())
        if state is None:
            return TurnRevocationResult(False, False, 0, "turn_credential_not_found")
        self._state.revoke(credential_id)
        hook = state.binding_mode == "authorization_hook"
        remaining = 0 if hook else max(0, state.expires_at_seconds - now)
        return TurnRevocationResult(True, hook, remaining, "turn_credential_revoked")

    def _validate_request(self, request: TurnCredentialRequest) -> None:
        for value in (
            request.tenant_ref,
            request.room_ref,
            request.participant_ref,
            request.device_ref,
            request.region,
            request.pool_id,
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
                raise WebrtcTurnCredentialError("turn_credential_scope_invalid")
        for value in (request.admission_epoch, request.requested_ttl_seconds, request.refresh_count):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WebrtcTurnCredentialError("turn_credential_request_invalid")
        if request.admission_epoch < 1 or request.requested_ttl_seconds < 1:
            raise WebrtcTurnCredentialError("turn_credential_request_invalid")
        if request.refresh_count > self._policy.credential_refresh_count_max:
            raise WebrtcTurnCredentialError("turn_credential_refresh_cap_exceeded", 429)

    def _claim_digests(self, request: TurnCredentialRequest, secret: bytes) -> dict[str, str]:
        values = {
            "tenant": request.tenant_ref,
            "room": request.room_ref,
            "participant": request.participant_ref,
            "device": request.device_ref,
            "region": request.region,
            "pool": request.pool_id,
        }
        return {
            name: hmac.new(secret, f"turn-claim-{name}-v1\0{value}".encode(), hashlib.sha256).hexdigest()
            for name, value in values.items()
        }

    @staticmethod
    def _sign_credential(secret: bytes, username: str, claims: Mapping[str, str]) -> str:
        message = username.encode("ascii") + b"\0" + "\0".join(
            f"{name}={value}" for name, value in sorted(claims.items())
        ).encode("ascii")
        return base64.b64encode(hmac.new(secret, message, hashlib.sha256).digest()).decode("ascii")

    @staticmethod
    def _admission_reason(value: str) -> str:
        allowed = {"turn_admission_revoked", "turn_quota_not_reserved", "turn_pool_forbidden"}
        return value if value in allowed else "turn_credential_admission_denied"


__all__ = [
    "InMemoryTurnCredentialStatePort",
    "InMemoryTurnSigningKeyRing",
    "TurnAdmissionAuthorityPort",
    "TurnAdmissionDecision",
    "TurnCredentialBundle",
    "TurnCredentialPolicy",
    "TurnCredentialRequest",
    "TurnCredentialStatePort",
    "TurnRevocationResult",
    "TurnSigningKey",
    "TurnSigningKeyPort",
    "WebrtcTurnCredentialError",
    "WebrtcTurnCredentialService",
]
