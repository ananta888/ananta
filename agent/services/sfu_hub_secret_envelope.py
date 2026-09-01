"""Hub-only authenticated envelope protection for sensitive SFU state.

The database stores key identifiers, nonces and authenticated ciphertext only.
Key bytes remain behind this port and are deliberately excluded from repr,
logs, evidence and runtime adapter contracts.  A key ring permits bounded
online re-wrapping while the active key id remains explicit.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from agent.models.sfu_group_keys import (
    SfuHubBlindIndex,
    SfuHubSealedSecret,
    SfuHubSecretEnvelopeError,
)
from agent.ports.sfu_group_keys import SfuHubSecretEnvelopePort

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AesGcmSfuHubSecretEnvelope:
    """Authenticated envelope implementation with explicit rotation keys."""

    def __init__(
        self,
        keys: Mapping[str, bytes],
        *,
        active_key_id: str,
        blind_keys: Mapping[str, bytes] | None = None,
        active_blind_key_id: str | None = None,
    ) -> None:
        if _KEY_ID.fullmatch(active_key_id) is None or active_key_id not in keys:
            raise SfuHubSecretEnvelopeError("sfu_secret_active_key_invalid")
        normalized = {str(key_id): bytes(key) for key_id, key in keys.items()}
        if any(_KEY_ID.fullmatch(key_id) is None or len(key) < 32 for key_id, key in normalized.items()):
            raise SfuHubSecretEnvelopeError("sfu_secret_key_ring_invalid")
        self._keys = normalized
        self._active_key_id = active_key_id
        normalized_blind = {
            str(blind_id): bytes(blind_key)
            for blind_id, blind_key in (
                blind_keys or {f"legacy:{active_key_id}": normalized[active_key_id]}
            ).items()
        }
        selected_blind = active_blind_key_id or next(iter(normalized_blind), "")
        if (
            _KEY_ID.fullmatch(selected_blind) is None
            or selected_blind not in normalized_blind
            or any(
                _KEY_ID.fullmatch(blind_id) is None or len(blind_key) < 32
                for blind_id, blind_key in normalized_blind.items()
            )
        ):
            raise SfuHubSecretEnvelopeError("sfu_secret_blind_key_ring_invalid")
        self._blind_keys = normalized_blind
        self._active_blind_key_id = selected_blind

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def blind(self, *, purpose: str, scope: str, value: str) -> str:
        return self.blind_index(purpose=purpose, scope=scope, value=value).digest

    def blind_index(self, *, purpose: str, scope: str, value: str) -> SfuHubBlindIndex:
        _context(purpose, scope)
        if not isinstance(value, str) or not value:
            raise SfuHubSecretEnvelopeError("sfu_secret_blind_value_invalid")
        key = self._derive_blind(
            self._active_blind_key_id, purpose=f"blind:{purpose}", scope=scope
        )
        return SfuHubBlindIndex(
            self._active_blind_key_id,
            hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest(),
        )

    def blind_candidates(
        self, *, purpose: str, scope: str, value: str
    ) -> tuple[SfuHubBlindIndex, ...]:
        _context(purpose, scope)
        if not isinstance(value, str) or not value:
            raise SfuHubSecretEnvelopeError("sfu_secret_blind_value_invalid")
        candidates: list[SfuHubBlindIndex] = []
        seen: set[str] = set()
        for blind_id in self._blind_keys:
            key = self._derive_blind(blind_id, purpose=f"blind:{purpose}", scope=scope)
            digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
            if digest not in seen:
                candidates.append(SfuHubBlindIndex(blind_id, digest))
                seen.add(digest)
        for wrapping_id in self._keys:
            key = self._derive(wrapping_id, purpose=f"blind:{purpose}", scope=scope)
            digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
            if digest not in seen:
                candidates.append(SfuHubBlindIndex(f"legacy:{wrapping_id}", digest))
                seen.add(digest)
        return tuple(candidates)

    @property
    def active_blind_key_id(self) -> str:
        return self._active_blind_key_id

    def seal(
        self,
        plaintext: bytes,
        *,
        purpose: str,
        scope: str,
        aad: bytes,
    ) -> SfuHubSealedSecret:
        _context(purpose, scope)
        if not isinstance(plaintext, bytes) or not plaintext or not isinstance(aad, bytes) or not aad:
            raise SfuHubSecretEnvelopeError("sfu_secret_plaintext_invalid")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._derive(self._active_key_id, purpose=purpose, scope=scope)).encrypt(
            nonce, plaintext, aad
        )
        return SfuHubSealedSecret(self._active_key_id, nonce, ciphertext)

    def open(
        self,
        envelope: SfuHubSealedSecret,
        *,
        purpose: str,
        scope: str,
        aad: bytes,
    ) -> bytes:
        _context(purpose, scope)
        if envelope.key_id not in self._keys:
            raise SfuHubSecretEnvelopeError("sfu_secret_key_unavailable")
        try:
            return AESGCM(self._derive(envelope.key_id, purpose=purpose, scope=scope)).decrypt(
                envelope.nonce, envelope.ciphertext, aad
            )
        except InvalidTag as exc:
            raise SfuHubSecretEnvelopeError("sfu_secret_authentication_failed") from exc

    def rewrap(
        self,
        envelope: SfuHubSealedSecret,
        *,
        purpose: str,
        scope: str,
        aad: bytes,
    ) -> SfuHubSealedSecret:
        if envelope.key_id == self._active_key_id:
            return envelope
        plaintext = self.open(envelope, purpose=purpose, scope=scope, aad=aad)
        try:
            return self.seal(plaintext, purpose=purpose, scope=scope, aad=aad)
        finally:
            plaintext = b""

    def _derive(self, key_id: str, *, purpose: str, scope: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(b"ananta:sfu-hub-envelope:v1").digest(),
            # Keep the v1 wrapping derivation byte-for-byte compatible. NUL is
            # rejected by _context(), so the legacy delimiter remains unambiguous.
            info=f"{key_id}\0{purpose}\0{scope}".encode("utf-8"),
        ).derive(self._keys[key_id])

    def _derive_blind(self, key_id: str, *, purpose: str, scope: str) -> bytes:
        if key_id.startswith("legacy:"):
            legacy_id = key_id[7:]
            return HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=hashlib.sha256(b"ananta:sfu-hub-envelope:v1").digest(),
                info=f"{legacy_id}\0{purpose}\0{scope}".encode("utf-8"),
            ).derive(self._blind_keys[key_id])
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(b"ananta:sfu-hub-blind-index:v1").digest(),
            info=_derive_context(key_id, purpose, scope),
        ).derive(self._blind_keys[key_id])


def derive_sfu_hub_envelope(
    master_secret: str,
    *,
    key_id: str = "sfu-hub-v1",
    blind_key_id: str = "sfu-hub-v1",
) -> AesGcmSfuHubSecretEnvelope:
    if not isinstance(master_secret, str) or len(master_secret.encode("utf-8")) < 32:
        raise SfuHubSecretEnvelopeError("sfu_secret_master_key_invalid")
    root = hashlib.sha256(b"ananta:sfu-hub-master:v1\0" + master_secret.encode("utf-8")).digest()
    return AesGcmSfuHubSecretEnvelope(
        {key_id: root},
        active_key_id=key_id,
        blind_keys={f"legacy:{blind_key_id}": root},
        active_blind_key_id=f"legacy:{blind_key_id}",
    )


def _context(purpose: str, scope: str) -> None:
    if (
        not purpose
        or not scope
        or "\0" in purpose
        or "\0" in scope
        or len(purpose) > 128
        or len(scope) > 512
    ):
        raise SfuHubSecretEnvelopeError("sfu_secret_scope_invalid")


def _derive_context(key_id: str, purpose: str, scope: str) -> bytes:
    parts = tuple(value.encode("utf-8") for value in (key_id, purpose, scope))
    return b"".join(len(value).to_bytes(4, "big") + value for value in parts)


__all__ = [
    "AesGcmSfuHubSecretEnvelope",
    "SfuHubBlindIndex",
    "SfuHubSealedSecret",
    "SfuHubSecretEnvelopeError",
    "SfuHubSecretEnvelopePort",
    "derive_sfu_hub_envelope",
]
