"""Strict pair security primitives for the standalone rendezvous boundary.

The service authenticates membership and device public keys.  It never sees
the derived ECDH secret or encrypted application/media payloads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


TENANT_ID = "public-ananta"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def spki_fingerprint(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("device_key_invalid") from exc
    if not 64 <= len(raw) <= 512:
        raise ValueError("device_key_invalid")
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
        self._contract_key = hashlib.sha256(
            b"ananta.public-rendezvous.contract.v1\0" + secret.encode()
        ).digest()

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
            "payload_classes": ["bulk", "control", "media", "semantic"],
            "expires_at_ms": int(float(session["expires_at"]) * 1000),
        }
        offer = {**common, "sender_id": owner["membership_id"], "recipient_id": guest["membership_id"]}
        answer = {**common, "sender_id": guest["membership_id"], "recipient_id": owner["membership_id"]}
        digest = hashlib.sha256(canonical({
            "domain": "ananta.webrtc.security-negotiation.v1", "offer": offer, "answer": answer,
        })).hexdigest()
        signature = hmac.new(self._contract_key, digest.encode("ascii"), hashlib.sha256).hexdigest()
        return {
            "version": 1, "negotiation_id": negotiation_id, "offer": offer, "answer": answer,
            "digest": digest, "signature": signature, "signature_algorithm": "HMAC-SHA256",
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
        package_id = hashlib.sha256(canonical({
            "domain": "ananta.webrtc.peer-key-package-id.v1",
            "membership_id": membership["membership_id"],
            "membership_version": membership["membership_version"],
            "recipient_peer_id": recipient_peer_id,
            "epoch": session["security_epoch"],
            "device_key_fingerprint": membership["fingerprint"],
            "security_contract_digest": contract_digest,
        })).hexdigest()
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
