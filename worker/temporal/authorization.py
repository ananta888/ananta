"""Dependency-free verifier for Hub-issued runtime authorization envelopes."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from ananta_contracts.runtime_authorization_crypto import (
    ED25519_VERIFICATION_KEYRING_SCHEMA,
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)
from ananta_contracts.temporal_workflow import (
    AuthorizationEnvelopeRef,
    TemporalContractError,
)


class RuntimeAuthorizationVerifier:
    """Verify the neutral wire contract without importing the Hub package."""

    def __init__(
        self,
        *,
        keys: Mapping[str, str | bytes],
        active_key_id: str,
        revoked_key_ids: tuple[str, ...] = (),
        revoked_envelope_ids: tuple[str, ...] = (),
        verification_key_ring: Ed25519VerificationKeyRing | None = None,
    ) -> None:
        self._verification_key_ring = verification_key_ring
        if verification_key_ring is not None:
            self._keys: dict[str, bytes] = {}
            self._revoked_key_ids = frozenset()
            self._revoked_envelope_ids = frozenset()
            return
        normalized = {
            str(key_id): value.encode("utf-8") if isinstance(value, str) else bytes(value)
            for key_id, value in keys.items()
        }
        if str(active_key_id) not in normalized:
            raise ValueError("active_signing_key_missing")
        if any(len(value) < 16 for value in normalized.values()):
            raise ValueError("signing_key_too_short")
        self._keys = normalized
        self._revoked_key_ids = frozenset(str(value) for value in revoked_key_ids)
        self._revoked_envelope_ids = frozenset(str(value) for value in revoked_envelope_ids)

    @classmethod
    def from_config_mapping(
        cls,
        raw: Mapping[str, object],
        *,
        allow_legacy_hmac: bool = False,
    ) -> "RuntimeAuthorizationVerifier":
        if str(raw.get("schema") or "") == ED25519_VERIFICATION_KEYRING_SCHEMA:
            try:
                verification = Ed25519VerificationKeyRing.from_mapping(raw)
            except RuntimeAuthorizationCryptoError as exc:
                raise ValueError(exc.reason_code) from exc
            return cls(
                keys={},
                active_key_id="",
                verification_key_ring=verification,
            )
        if not allow_legacy_hmac:
            raise ValueError("legacy_hmac_authorization_keyring_disabled")
        keys = raw.get("keys")
        if not isinstance(keys, Mapping):
            raise ValueError("authorization keyring keys are required")
        return cls(
            keys={str(key): str(value) for key, value in keys.items()},
            active_key_id=str(raw.get("active_key_id") or ""),
            revoked_key_ids=_string_tuple(raw.get("revoked_key_ids")),
            revoked_envelope_ids=_string_tuple(raw.get("revoked_envelope_ids")),
        )

    def verify(self, envelope: AuthorizationEnvelopeRef, *, now: float) -> None:
        timestamp = float(now)
        if timestamp < envelope.issued_at:
            self._deny("authorization_not_yet_valid")
        if timestamp >= envelope.expires_at:
            self._deny("authorization_expired")
        payload = envelope.to_dict()
        payload.pop("signature", None)
        if self._verification_key_ring is not None:
            try:
                self._verification_key_ring.verify(
                    namespace=envelope.schema,
                    payload=payload,
                    key_id=envelope.key_id,
                    signature=envelope.signature,
                    contract_id=envelope.envelope_id,
                )
            except RuntimeAuthorizationCryptoError as exc:
                self._deny(exc.reason_code)
            return
        if envelope.envelope_id in self._revoked_envelope_ids:
            self._deny("signed_contract_revoked")
        if envelope.key_id in self._revoked_key_ids:
            self._deny("signing_key_revoked")
        key = self._keys.get(envelope.key_id)
        if key is None:
            self._deny("signing_key_unknown")
        try:
            canonical = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise TemporalContractError(
                "authorization_payload_invalid",
                "runtime authorization verification failed",
            ) from exc
        message = f"{envelope.schema}\n{canonical}".encode("utf-8")
        expected = hmac.new(key, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, envelope.signature):
            self._deny("signature_invalid")

    @staticmethod
    def _deny(reason_code: str) -> None:
        raise TemporalContractError(reason_code, "runtime authorization verification failed")


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("revocation list must be a sequence")
    return tuple(str(item) for item in value if str(item).strip())


__all__ = ["RuntimeAuthorizationVerifier"]
