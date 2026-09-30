from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$")


@dataclass(frozen=True)
class VoicePrincipal:
    tenant_id: str
    subject: str

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id, field="tenant_id", max_length=128)
        validate_identifier(self.subject, field="subject", max_length=128)


@dataclass(frozen=True)
class VoiceGovernanceError(Exception):
    code: str
    message: str
    status_code: int = 400


def validate_identifier(value: Any, *, field: str, max_length: int = 128) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length or _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise VoiceGovernanceError(
            code="voice_governance.invalid_identifier",
            message=f"{field} must contain 1-{max_length} safe identifier characters",
            status_code=422,
        )
    return normalized


def validate_text(value: Any, *, field: str, max_length: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise VoiceGovernanceError(
                code="voice_governance.missing_field",
                message=f"{field} is required",
                status_code=422,
            )
        return None
    normalized = str(value).strip()
    if required and not normalized:
        raise VoiceGovernanceError(
            code="voice_governance.missing_field",
            message=f"{field} is required",
            status_code=422,
        )
    if len(normalized) > max_length:
        raise VoiceGovernanceError(
            code="voice_governance.value_too_long",
            message=f"{field} exceeds {max_length} characters",
            status_code=422,
        )
    return normalized or None


def stable_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def voice_scope_digest(principal: VoicePrincipal, profile_id: str) -> str:
    """Return a content-free audit correlation key for one Voice scope."""

    normalized_profile_id = validate_identifier(profile_id, field="profile_id")
    canonical = f"{principal.tenant_id}\0{principal.subject}\0{normalized_profile_id}"
    return hmac.new(_voice_privacy_hmac_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def voice_idempotency_key_digest(
    idempotency_key: str,
    *,
    scope_digest: str,
    operation: str,
) -> str:
    normalized_key = validate_text(
        idempotency_key,
        field="idempotency_key",
        max_length=160,
        required=True,
    )
    return hmac.new(
        _voice_privacy_hmac_key(),
        f"idempotency\0{operation}\0{scope_digest}\0{normalized_key}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def voice_idempotency_audio_binding(
    principal: VoicePrincipal,
    *,
    operation: str,
    idempotency_key: str,
    audio: bytes,
) -> str:
    """Bind one idempotency key to audio without a reusable fingerprint.

    The derived key includes tenant, owner, operation and the caller's
    idempotency key. Consequently the same audio produces unrelated values for
    different operations or keys and cannot be used for cross-request audio
    correlation.
    """

    normalized_key = validate_text(
        idempotency_key,
        field="idempotency_key",
        max_length=160,
        required=True,
    )
    scope_key = hmac.new(
        _voice_privacy_hmac_key(),
        (
            f"voice-idempotency-audio-v1\0{principal.tenant_id}\0{principal.subject}"
            f"\0{operation}\0{normalized_key}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    digest = hmac.new(scope_key, bytes(audio), hashlib.sha256).hexdigest()
    return f"idem-audio-v1:{digest}"


def voice_idempotency_storage_key(
    principal: VoicePrincipal,
    *,
    operation: str,
    idempotency_key: str,
) -> str:
    """Return the non-reversible database key for an idempotency scope."""

    normalized_key = validate_text(
        idempotency_key,
        field="idempotency_key",
        max_length=160,
        required=True,
    )
    return hmac.new(
        _voice_privacy_hmac_key(),
        (
            f"voice-idempotency-storage-v1\0{principal.tenant_id}\0{principal.subject}"
            f"\0{operation}\0{normalized_key}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _voice_privacy_hmac_key() -> bytes:
    from agent.config import settings

    secret = str(settings.secret_key or "")
    if not secret:
        raise VoiceGovernanceError(
            code="voice_privacy.hmac_key_missing",
            message="voice deletion ledger key is not configured",
            status_code=500,
        )
    return hashlib.sha256(f"ananta-voice-deletion-ledger-v1:{secret}".encode("utf-8")).digest()


def voice_deletion_ledger_signature(canonical_record: bytes) -> str:
    return hmac.new(_voice_privacy_hmac_key(), canonical_record, hashlib.sha256).hexdigest()
