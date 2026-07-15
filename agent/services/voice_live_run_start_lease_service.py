from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import jwt

from agent.config import settings
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.services.voice_governance_domain import (
    VoicePrincipal,
    validate_identifier,
    voice_scope_digest,
)

_LEASE_TTL_SECONDS = 600
_ISSUER = "ananta-hub"
_AUDIENCE = "ananta-voice-live-run-start"
_PURPOSE = "voice_live_run_profile_start"
_SIGNING_CONTEXT = b"ananta.voice-live-run-start-lease.signing.v1"
_GENERATION_RE = re.compile(r"^[0-9a-f]{64}$")


class VoiceLiveRunStartLeaseError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class VoiceLiveRunStartLease:
    lease_token: str
    profile_id: str
    generation: str
    issued_at: float
    expires_at: float


class VoiceLiveRunStartLeaseService:
    """Issues stateless Hub grants fenced to one profile-deletion generation."""

    def __init__(
        self,
        tombstones: VoiceDeletionTombstoneRepository | None = None,
        *,
        clock: Callable[[], float] = time.time,
        signing_secret: str | None = None,
        ttl_seconds: int = _LEASE_TTL_SECONDS,
    ) -> None:
        self._tombstones = tombstones or VoiceDeletionTombstoneRepository()
        self._clock = clock
        self._signing_secret = str(signing_secret or settings.secret_key)
        self._ttl_seconds = max(300, min(int(ttl_seconds), 600))

    def issue(
        self,
        principal: VoicePrincipal,
        profile_id: str,
    ) -> VoiceLiveRunStartLease:
        normalized_profile_id = validate_identifier(profile_id or "default", field="profile_id")
        issued_at = float(self._clock())
        expires_at = issued_at + self._ttl_seconds
        generation = self._tombstones.generation(principal, normalized_profile_id)
        scope_digest = voice_scope_digest(principal, normalized_profile_id)
        token = jwt.encode(
            {
                "iss": _ISSUER,
                "aud": _AUDIENCE,
                "purpose": _PURPOSE,
                "scope_digest": scope_digest,
                "generation": generation,
                "iat": issued_at,
                "exp": expires_at,
                "jti": uuid.uuid4().hex,
            },
            self._signing_key(),
            algorithm="HS256",
        )
        return VoiceLiveRunStartLease(
            lease_token=token,
            profile_id=normalized_profile_id,
            generation=generation,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def verify(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        lease_token: str,
    ) -> VoiceLiveRunStartLease:
        normalized_profile_id = validate_identifier(profile_id or "default", field="profile_id")
        token = str(lease_token or "").strip()
        if not token:
            raise VoiceLiveRunStartLeaseError(
                "voice_live_run.start_lease_required",
                "lease_token from POST /v1/voice/live-runs/lease is required",
                422,
            )
        if len(token) > 4_096:
            self._invalid()
        try:
            claims = jwt.decode(
                token,
                self._signing_key(),
                algorithms=["HS256"],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "purpose",
                        "scope_digest",
                        "generation",
                        "iat",
                        "exp",
                        "jti",
                    ],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise self._invalid_error() from exc
        if claims.get("purpose") != _PURPOSE:
            self._invalid()
        try:
            issued_at = float(claims["iat"])
            expires_at = float(claims["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_error() from exc
        now = float(self._clock())
        if not all(math.isfinite(value) for value in (issued_at, expires_at)):
            self._invalid()
        if issued_at > now + 5 or expires_at <= issued_at or expires_at - issued_at > 600:
            self._invalid()
        if expires_at <= now:
            raise VoiceLiveRunStartLeaseError(
                "voice_live_run.start_lease_expired",
                "voice live-run start lease expired; request a new lease",
                409,
            )
        expected_scope = voice_scope_digest(principal, normalized_profile_id)
        actual_scope = str(claims.get("scope_digest") or "")
        if not hmac.compare_digest(actual_scope, expected_scope):
            raise VoiceLiveRunStartLeaseError(
                "voice_live_run.start_lease_scope_mismatch",
                "voice live-run start lease belongs to a different principal or profile",
                403,
            )
        generation = str(claims.get("generation") or "")
        if _GENERATION_RE.fullmatch(generation) is None:
            self._invalid()
        self.assert_generation(
            principal,
            normalized_profile_id,
            expected_generation=generation,
        )
        return VoiceLiveRunStartLease(
            lease_token=token,
            profile_id=normalized_profile_id,
            generation=generation,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def assert_generation(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        expected_generation: str,
    ) -> None:
        current_generation = self._tombstones.generation(principal, profile_id)
        if not hmac.compare_digest(current_generation, expected_generation):
            raise VoiceLiveRunStartLeaseError(
                "voice_live_run.start_lease_stale",
                "profile deletion changed after the live-run start lease was issued",
                409,
            )

    def _signing_key(self) -> bytes:
        return hmac.new(
            self._signing_secret.encode("utf-8"),
            _SIGNING_CONTEXT,
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _invalid_error() -> VoiceLiveRunStartLeaseError:
        return VoiceLiveRunStartLeaseError(
            "voice_live_run.start_lease_invalid",
            "voice live-run start lease is invalid",
            403,
        )

    @classmethod
    def _invalid(cls) -> None:
        raise cls._invalid_error()


voice_live_run_start_lease_service = VoiceLiveRunStartLeaseService()


def get_voice_live_run_start_lease_service() -> VoiceLiveRunStartLeaseService:
    return voice_live_run_start_lease_service
