"""Strict pair security primitives for the standalone rendezvous boundary.

The service authenticates membership and device public keys.  It never sees
the derived ECDH secret or encrypted application payloads. Public audio/video
remains disabled unless both peers negotiate one exact, separately signed
media contract and enforce it through browser encoded-media transforms.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TENANT_ID = "public-ananta"
PUBLIC_MEDIA_E2EE_VERSION = 1
PUBLIC_MEDIA_E2EE_VERSION_V2 = 2
SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS = (
    PUBLIC_MEDIA_E2EE_VERSION,
    PUBLIC_MEDIA_E2EE_VERSION_V2,
)
PUBLIC_MEDIA_TRANSFORM = "RTCRtpScriptTransform"
PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 = "ananta.public-pair.media-frame.v2"
PUBLIC_MEDIA_GRANTS = (
    "microphone-opus",
    "camera-vp8",
    "screen-vp8",
)
PUBLIC_MEDIA_SLOTS = (
    {"slot": "microphone-opus", "kind": "audio", "codec": "opus"},
    {"slot": "camera-vp8", "kind": "video", "codec": "vp8"},
    {"slot": "screen-vp8", "kind": "video", "codec": "vp8"},
)


def public_media_capabilities_v1() -> dict[str, Any]:
    """Return the one closed capability advertisement supported by v1."""
    return {
        "version": PUBLIC_MEDIA_E2EE_VERSION,
        "transform": PUBLIC_MEDIA_TRANSFORM,
        "grants": list(PUBLIC_MEDIA_GRANTS),
    }


def public_media_capabilities_v2() -> dict[str, Any]:
    """Return the one closed capability advertisement supported by v2."""
    return {
        "version": PUBLIC_MEDIA_E2EE_VERSION_V2,
        "transform": PUBLIC_MEDIA_TRANSFORM,
        "frame_format": PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
        "grants": list(PUBLIC_MEDIA_GRANTS),
    }


def public_media_capabilities_for_version(version: int) -> dict[str, Any] | None:
    """Project a persisted media version back to its immutable capability."""
    if type(version) is not int:
        return None
    if version == PUBLIC_MEDIA_E2EE_VERSION:
        return public_media_capabilities_v1()
    if version == PUBLIC_MEDIA_E2EE_VERSION_V2:
        return public_media_capabilities_v2()
    return None


def normalize_public_media_advertisement(version: Any, capabilities: Any) -> int:
    """Validate an optional, fail-closed Public Pair media advertisement.

    Version zero is the backward-compatible data-only state. Media versions
    are accepted only with their complete closed capability object, so a
    partial, mixed, or future-shaped request can never broaden a grant or
    silently select a wire-incompatible framing format.
    """
    normalized_version = 0 if version is None else version
    if (
        isinstance(normalized_version, bool)
        or not isinstance(normalized_version, int)
        or normalized_version not in {0, *SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS}
    ):
        raise ValueError("public_media_e2ee_version_unsupported")
    if normalized_version == 0:
        if capabilities is not None:
            raise ValueError("public_media_capabilities_without_version")
        return 0
    expected = public_media_capabilities_for_version(normalized_version)
    if expected is None:
        raise ValueError("public_media_e2ee_version_unsupported")
    if not isinstance(capabilities, dict) or set(capabilities) != set(expected):
        raise ValueError("public_media_capabilities_invalid")
    if (
        type(capabilities.get("version")) is not int
        or capabilities.get("version") != normalized_version
        or type(capabilities.get("transform")) is not str
        or capabilities.get("transform") != PUBLIC_MEDIA_TRANSFORM
        or not isinstance(capabilities.get("grants"), list)
        or any(type(grant) is not str for grant in capabilities["grants"])
        or capabilities["grants"] != list(PUBLIC_MEDIA_GRANTS)
        or (
            normalized_version == PUBLIC_MEDIA_E2EE_VERSION_V2
            and (
                type(capabilities.get("frame_format")) is not str
                or capabilities.get("frame_format") != PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
            )
        )
    ):
        raise ValueError("public_media_capabilities_invalid")
    return normalized_version


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def spki_fingerprint(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("device_key_invalid") from exc
    if not 64 <= len(raw) <= 512:
        raise ValueError("device_key_invalid")
    try:
        public_key = serialization.load_der_public_key(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("device_key_invalid") from exc
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(public_key.curve, ec.SECP256R1):
        raise ValueError("device_key_algorithm_unsupported")
    return hashlib.sha256(raw).hexdigest()


class PairSecurityAuthority:
    """Issues short-lived, addressed key packages from current membership."""

    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("rendezvous_security_signing_secret_too_short")
        seed = hashlib.sha256(b"ananta.public-rendezvous.identity.v1\0" + secret.encode()).digest()
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_b64 = base64.b64encode(public).decode("ascii")
        self.key_id = "rv:" + hashlib.sha256(public).hexdigest()[:24]
        self._contract_key = hashlib.sha256(b"ananta.public-rendezvous.contract.v1\0" + secret.encode()).digest()

    def require_key_id(self, expected_key_id: str) -> None:
        """Fail closed when deployment policy pins a different authority."""
        expected = str(expected_key_id or "").strip()
        if expected and not hmac.compare_digest(self.key_id, expected):
            raise RuntimeError("rendezvous_security_signing_key_id_mismatch")

    def contract(self, session: dict[str, Any], owner: dict[str, Any], guest: dict[str, Any]) -> dict[str, Any]:
        epoch = int(session["security_epoch"])
        transcript = {
            "domain": "ananta.share.strict-pair-negotiation-id.v1",
            "epoch": epoch,
            "owner_membership_id": owner["membership_id"],
            "recipient_membership_id": guest["membership_id"],
            "scope_id": session["id"],
            "tenant_id": TENANT_ID,
        }
        negotiation_id = "neg:" + hashlib.sha256(canonical(transcript)).hexdigest()
        common = {
            "version": 1,
            "negotiation_id": negotiation_id,
            "scope_kind": "session",
            "scope_id": session["id"],
            "minimum_mode": "strict_e2ee",
            "selected_mode": "strict_e2ee",
            "algorithms": ["AES-256-GCM", "ECDH-P256-HKDF-SHA256"],
            "key_epoch": epoch,
            "payload_classes": ["bulk", "control", "semantic"],
            "expires_at_ms": int(float(session["expires_at"]) * 1000),
        }
        offer = {**common, "sender_id": owner["membership_id"], "recipient_id": guest["membership_id"]}
        answer = {**common, "sender_id": guest["membership_id"], "recipient_id": owner["membership_id"]}
        digest = hashlib.sha256(
            canonical(
                {
                    "domain": "ananta.webrtc.security-negotiation.v1",
                    "offer": offer,
                    "answer": answer,
                }
            )
        ).hexdigest()
        signature = hmac.new(self._contract_key, digest.encode("ascii"), hashlib.sha256).hexdigest()
        return {
            "version": 1,
            "negotiation_id": negotiation_id,
            "offer": offer,
            "answer": answer,
            "digest": digest,
            "signature": signature,
            "signature_algorithm": "HMAC-SHA256",
        }

    def public_media_contract(
        self,
        *,
        session: dict[str, Any],
        owner: dict[str, Any],
        guest: dict[str, Any],
        base_security_contract_digest: str,
        public_media_e2ee_version: int = PUBLIC_MEDIA_E2EE_VERSION,
    ) -> dict[str, Any]:
        """Issue the stable, separately signed Public Pair media contract."""
        if (
            type(public_media_e2ee_version) is not int
            or public_media_e2ee_version not in SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS
        ):
            raise ValueError("public_media_e2ee_version_unsupported")
        unsigned = {
            "domain": f"ananta.public-pair.media-security-contract.v{public_media_e2ee_version}",
            "version": public_media_e2ee_version,
            "session_id": session["id"],
            "epoch": int(session["security_epoch"]),
            "identity_binding_version": 2,
            "base_security_contract_digest": base_security_contract_digest,
            "memberships": [
                {
                    "membership_id": member["membership_id"],
                    "membership_version": int(member["membership_version"]),
                    "peer_id": member["peer_id"],
                    "device_key_fingerprint": member["fingerprint"],
                    "public_media_e2ee_version": int(member["public_media_e2ee_version"]),
                }
                for member in (owner, guest)
            ],
            "grants": list(PUBLIC_MEDIA_GRANTS),
            "slots": [dict(slot) for slot in PUBLIC_MEDIA_SLOTS],
            "transform": PUBLIC_MEDIA_TRANSFORM,
            "algorithms": {
                "aead": "AES-256-GCM",
                "kdf": "HKDF-SHA-256",
            },
            "expires_at_ms": int(float(session["expires_at"]) * 1000),
            "authority_key_id": self.key_id,
        }
        if public_media_e2ee_version == PUBLIC_MEDIA_E2EE_VERSION_V2:
            unsigned["frame_format"] = PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
        digest = hashlib.sha256(canonical(unsigned)).hexdigest()
        signed = {
            **unsigned,
            "digest": digest,
            "signature_algorithm": "Ed25519",
        }
        return {
            **signed,
            "signature_b64": base64.b64encode(self._private_key.sign(canonical(signed))).decode("ascii"),
        }

    def key_package(
        self,
        *,
        session: dict[str, Any],
        membership: dict[str, Any],
        recipient_peer_id: str,
        contract_digest: str,
    ) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        expires_at_ms = min(int(float(session["expires_at"]) * 1000), now_ms + 5 * 60 * 1000)
        package_id = hashlib.sha256(
            canonical(
                {
                    "domain": "ananta.webrtc.peer-key-package-id.v1",
                    "membership_id": membership["membership_id"],
                    "membership_version": membership["membership_version"],
                    "recipient_peer_id": recipient_peer_id,
                    "epoch": session["security_epoch"],
                    "device_key_fingerprint": membership["fingerprint"],
                    "security_contract_digest": contract_digest,
                }
            )
        ).hexdigest()
        package = {
            "version": 1,
            "package_id": package_id,
            "membership_id": membership["membership_id"],
            "membership_version": membership["membership_version"],
            "tenant_id": TENANT_ID,
            "scope_kind": "session",
            "scope_id": session["id"],
            "epoch": session["security_epoch"],
            "peer_id": membership["peer_id"],
            "recipient_peer_id": recipient_peer_id,
            "device_id": membership["device_id"],
            "device_key_fingerprint": membership["fingerprint"],
            "ecdh_public_key_spki_b64": membership["public_key_spki_b64"],
            "issued_at_ms": now_ms,
            "expires_at_ms": expires_at_ms,
            "hub_key_id": self.key_id,
            "security_contract_digest": contract_digest,
        }
        package["signature_b64"] = base64.b64encode(self._private_key.sign(canonical(package))).decode("ascii")
        return package
