"""Hub-signed peer/device key packages for WebRTC E2EE sessions."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict, dataclass, replace
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


@dataclass(frozen=True)
class PeerMembership:
    membership_id: str
    tenant_id: str
    scope_kind: str
    scope_id: str
    peer_id: str
    device_id: str
    membership_version: int
    active: bool


@dataclass(frozen=True)
class SignedPeerKeyPackage:
    version: int
    package_id: str
    membership_id: str
    membership_version: int
    tenant_id: str
    scope_kind: str
    scope_id: str
    epoch: int
    peer_id: str
    recipient_peer_id: str
    device_id: str
    device_key_fingerprint: str
    ecdh_public_key_spki_b64: str
    issued_at_ms: int
    expires_at_ms: int
    hub_key_id: str
    security_contract_digest: str
    signature_b64: str = ""

    def unsigned_dict(self) -> dict[str, object]:
        raw = asdict(self)
        raw.pop("signature_b64")
        return raw


class PeerIdentityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def derive_hub_identity_key(secret: bytes) -> Ed25519PrivateKey:
    if len(secret) < 32:
        raise ValueError("hub_identity_secret_too_short")
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(secret).digest())


class WebrtcPeerIdentityService:
    """Signs only packages backed by current Hub membership and device state."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        hub_key_id: str,
        membership_lookup: Callable[[str], PeerMembership | None],
        device_fingerprint_lookup: Callable[[str, str], str | None],
        clock=time.time,
    ) -> None:
        self._private_key = private_key
        self._hub_key_id = hub_key_id
        self._membership_lookup = membership_lookup
        self._device_fingerprint_lookup = device_fingerprint_lookup
        self._clock = clock

    @property
    def hub_key_id(self) -> str:
        return self._hub_key_id

    def hub_public_key_b64(self) -> str:
        from cryptography.hazmat.primitives import serialization

        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def issue_key_package(
        self,
        *,
        membership_id: str,
        recipient_peer_id: str,
        epoch: int,
        ecdh_public_key_spki_b64: str,
        security_contract_digest: str,
        expires_at_ms: int,
    ) -> SignedPeerKeyPackage:
        membership = self._membership_lookup(membership_id)
        now_ms = int(self._clock() * 1000)
        if membership is None or not membership.active:
            raise PeerIdentityError("membership_stale")
        if recipient_peer_id == membership.peer_id:
            raise PeerIdentityError("reflection_detected")
        if epoch < 1:
            raise PeerIdentityError("epoch_invalid")
        if not now_ms < expires_at_ms <= now_ms + 10 * 60 * 1000:
            raise PeerIdentityError("key_package_expiry_invalid")
        if len(security_contract_digest) != 64 or any(
            char not in "0123456789abcdef" for char in security_contract_digest
        ):
            raise PeerIdentityError("security_contract_digest_invalid")
        known_fingerprint = self._device_fingerprint_lookup(membership.peer_id, membership.device_id)
        if not known_fingerprint:
            raise PeerIdentityError("device_unknown")
        actual_fingerprint = spki_fingerprint(ecdh_public_key_spki_b64)
        if actual_fingerprint != known_fingerprint:
            raise PeerIdentityError("device_key_substitution")

        package = SignedPeerKeyPackage(
            version=1,
            package_id=derive_peer_key_package_id(
                membership_id=membership.membership_id,
                membership_version=membership.membership_version,
                recipient_peer_id=recipient_peer_id,
                epoch=epoch,
                device_key_fingerprint=actual_fingerprint,
                security_contract_digest=security_contract_digest,
            ),
            membership_id=membership.membership_id,
            membership_version=membership.membership_version,
            tenant_id=membership.tenant_id,
            scope_kind=membership.scope_kind,
            scope_id=membership.scope_id,
            epoch=epoch,
            peer_id=membership.peer_id,
            recipient_peer_id=recipient_peer_id,
            device_id=membership.device_id,
            device_key_fingerprint=actual_fingerprint,
            ecdh_public_key_spki_b64=ecdh_public_key_spki_b64,
            issued_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
            hub_key_id=self._hub_key_id,
            security_contract_digest=security_contract_digest,
        )
        signature = self._private_key.sign(_canonical(package.unsigned_dict()))
        return replace(package, signature_b64=base64.b64encode(signature).decode("ascii"))

    def verify_key_package(
        self,
        package: SignedPeerKeyPackage,
        *,
        expected_recipient_peer_id: str,
        expected_scope_id: str,
        expected_epoch: int,
    ) -> tuple[bool, str]:
        return verify_peer_key_package(
            package,
            hub_public_key=self._private_key.public_key(),
            membership_lookup=self._membership_lookup,
            expected_recipient_peer_id=expected_recipient_peer_id,
            expected_scope_id=expected_scope_id,
            expected_epoch=expected_epoch,
            now_ms=int(self._clock() * 1000),
        )


def verify_peer_key_package(
    package: SignedPeerKeyPackage,
    *,
    hub_public_key: Ed25519PublicKey,
    membership_lookup: Callable[[str], PeerMembership | None],
    expected_recipient_peer_id: str,
    expected_scope_id: str,
    expected_epoch: int,
    now_ms: int,
) -> tuple[bool, str]:
    try:
        signature = base64.b64decode(package.signature_b64, validate=True)
        hub_public_key.verify(signature, _canonical(package.unsigned_dict()))
    except (ValueError, InvalidSignature):
        return False, "key_package_signature_invalid"
    if package.expires_at_ms <= now_ms:
        return False, "key_package_expired"
    membership = membership_lookup(package.membership_id)
    if membership is None or not membership.active or membership.membership_version != package.membership_version:
        return False, "membership_stale"
    if package.peer_id == package.recipient_peer_id:
        return False, "reflection_detected"
    if package.recipient_peer_id != expected_recipient_peer_id:
        return False, "unknown_key_share"
    if package.scope_id != expected_scope_id:
        return False, "scope_mismatch"
    if package.epoch != expected_epoch:
        return False, "epoch_mismatch"
    if (
        membership.peer_id != package.peer_id
        or membership.device_id != package.device_id
        or membership.scope_id != package.scope_id
        or membership.tenant_id != package.tenant_id
    ):
        return False, "membership_binding_mismatch"
    try:
        if spki_fingerprint(package.ecdh_public_key_spki_b64) != package.device_key_fingerprint:
            return False, "device_key_substitution"
    except PeerIdentityError:
        return False, "device_key_invalid"
    return True, "ok"


def spki_fingerprint(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise PeerIdentityError("device_key_invalid") from exc
    if not 64 <= len(raw) <= 512:
        raise PeerIdentityError("device_key_invalid")
    return hashlib.sha256(raw).hexdigest()


def derive_peer_key_package_id(
    *,
    membership_id: str,
    membership_version: int,
    recipient_peer_id: str,
    epoch: int,
    device_key_fingerprint: str,
    security_contract_digest: str,
) -> str:
    """Bind confirmation identity to membership, key, peer, epoch and contract."""

    return hashlib.sha256(
        _canonical(
            {
                "domain": "ananta.webrtc.peer-key-package-id.v1",
                "membership_id": membership_id,
                "membership_version": membership_version,
                "recipient_peer_id": recipient_peer_id,
                "epoch": epoch,
                "device_key_fingerprint": device_key_fingerprint,
                "security_contract_digest": security_contract_digest,
            }
        )
    ).hexdigest()


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


__all__ = [
    "PeerIdentityError",
    "PeerMembership",
    "SignedPeerKeyPackage",
    "WebrtcPeerIdentityService",
    "derive_hub_identity_key",
    "derive_peer_key_package_id",
    "verify_peer_key_package",
    "spki_fingerprint",
]
