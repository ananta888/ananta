"""Authoritative strict pair and bounded-group security-contract composition.

The Share routes remain transport adapters.  This service turns the current
Hub-owned membership/epoch state into the canonical bilateral Offer/Answer
contract consumed by key packages and secure-envelope admission.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from agent.config import settings
from agent.services.webrtc_security_policy import WebrtcSecurityPolicy
from ananta_contracts.webrtc_security_negotiation import (
    SecurityNegotiationError,
    parse_security_proposal,
)

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_STRICT_ALGORITHMS = ["AES-256-GCM", "ECDH-P256-HKDF-SHA256"]
_STRICT_PAYLOAD_CLASSES = ["bulk", "control", "media", "semantic"]


class ShareSecurityNegotiationError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class ShareSecurityNegotiationService:
    """Finalize deterministic, non-downgradable contracts per membership epoch."""

    def __init__(self, policy: WebrtcSecurityPolicy) -> None:
        self._policy = policy

    def finalize_strict_pair(
        self,
        *,
        session_id: str,
        tenant_id: str,
        epoch: int,
        owner_peer_id: str,
        memberships: Iterable[Mapping[str, Any]],
        session_expires_at: float | None,
    ) -> dict[str, Any]:
        active = [dict(item) for item in memberships if item.get("active")]
        if len(active) != 2:
            raise ShareSecurityNegotiationError("strict_pair_cardinality_required")
        owners = [item for item in active if str(item.get("peer_id") or "") == owner_peer_id]
        if len(owners) != 1:
            raise ShareSecurityNegotiationError("strict_pair_owner_binding_invalid")
        owner = owners[0]
        recipient = next(item for item in active if item is not owner)
        if str(recipient.get("peer_id") or "") == owner_peer_id:
            raise ShareSecurityNegotiationError("strict_pair_peer_binding_invalid")

        owner_membership_id = str(owner.get("membership_id") or "")
        recipient_membership_id = str(recipient.get("membership_id") or "")
        transcript = {
            "domain": "ananta.share.strict-pair-negotiation-id.v1",
            "epoch": epoch,
            "owner_membership_id": owner_membership_id,
            "recipient_membership_id": recipient_membership_id,
            "scope_id": session_id,
            "tenant_id": tenant_id,
        }
        negotiation_id = "neg:" + hashlib.sha256(
            json.dumps(transcript, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        expires_at_ms = self._expiry_ms(session_expires_at)
        common: dict[str, Any] = {
            "version": 1,
            "negotiation_id": negotiation_id,
            "scope_kind": "session",
            "scope_id": session_id,
            "minimum_mode": "strict_e2ee",
            "selected_mode": "strict_e2ee",
            "algorithms": list(_STRICT_ALGORITHMS),
            "key_epoch": epoch,
            "payload_classes": list(_STRICT_PAYLOAD_CLASSES),
            "expires_at_ms": expires_at_ms,
        }
        try:
            offer = parse_security_proposal(
                {
                    **common,
                    "sender_id": owner_membership_id,
                    "recipient_id": recipient_membership_id,
                }
            )
            answer = parse_security_proposal(
                {
                    **common,
                    "sender_id": recipient_membership_id,
                    "recipient_id": owner_membership_id,
                }
            )
            final = self._policy.finalize(
                offer=offer,
                answer=answer,
                authoritative_epoch=epoch,
                tenant_id=tenant_id,
                user_id=owner_peer_id,
            )
        except SecurityNegotiationError as exc:
            raise ShareSecurityNegotiationError(exc.reason_code) from exc
        return {
            "version": 1,
            "negotiation_id": negotiation_id,
            "offer": offer.canonical_dict(),
            "answer": answer.canonical_dict(),
            "digest": final.digest,
            "signature": final.signature,
            "signature_algorithm": "HMAC-SHA256",
        }

    def finalize_strict_group(
        self,
        *,
        session_id: str,
        tenant_id: str,
        epoch: int,
        owner_peer_id: str,
        memberships: Iterable[Mapping[str, Any]],
        session_expires_at: float | None,
    ) -> dict[str, Any]:
        """Bind Hub-signed peer packages to one bounded group membership set.

        Pair negotiation has an Offer/Answer transcript and therefore remains
        deliberately bilateral.  A group does not invent N-party answers: the
        Hub canonicalizes the active membership set and every addressed peer
        package signs the resulting digest.  Content keys are still created
        and wrapped by the publisher; the Hub only authenticates membership.
        """

        active = sorted(
            (dict(item) for item in memberships if item.get("active")),
            key=lambda item: str(item.get("peer_id") or ""),
        )
        if not 3 <= len(active) <= 8:
            raise ShareSecurityNegotiationError("strict_group_cardinality_required")
        peer_ids = [str(item.get("peer_id") or "") for item in active]
        if len(set(peer_ids)) != len(peer_ids) or peer_ids.count(owner_peer_id) != 1:
            raise ShareSecurityNegotiationError("strict_group_membership_binding_invalid")
        members: list[dict[str, Any]] = []
        for item in active:
            membership_id = str(item.get("membership_id") or "")
            peer_id = str(item.get("peer_id") or "")
            device_id = str(item.get("device_id") or "")
            fingerprint = str(item.get("fingerprint") or "")
            membership_version = item.get("membership_version")
            if (
                not membership_id
                or not peer_id
                or not device_id
                or type(membership_version) is not int
                or membership_version < 1
                or len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)
            ):
                raise ShareSecurityNegotiationError("strict_group_membership_binding_invalid")
            members.append(
                {
                    "device_id": device_id,
                    "device_key_fingerprint": fingerprint,
                    "membership_id": membership_id,
                    "membership_version": membership_version,
                    "peer_id": peer_id,
                }
            )
        member_set_digest = _canonical_digest(members)
        contract = {
            "version": 1,
            "kind": "strict_group",
            "scope_kind": "session",
            "scope_id": session_id,
            "tenant_id": tenant_id,
            "owner_peer_id": owner_peer_id,
            "key_epoch": epoch,
            "minimum_mode": "strict_e2ee",
            "selected_mode": "strict_e2ee",
            "algorithms": list(_STRICT_ALGORITHMS),
            "payload_classes": list(_STRICT_PAYLOAD_CLASSES),
            "member_set_digest": member_set_digest,
            "members": members,
            "expires_at_ms": self._expiry_ms(session_expires_at),
            "authorization": "hub_signed_peer_packages",
        }
        return {**contract, "digest": _canonical_digest(contract)}

    @staticmethod
    def _expiry_ms(session_expires_at: float | None) -> int:
        if session_expires_at is None:
            return _MAX_SAFE_INTEGER
        if not isinstance(session_expires_at, (int, float)) or not math.isfinite(float(session_expires_at)):
            raise ShareSecurityNegotiationError("session_expiry_invalid")
        value = int(float(session_expires_at) * 1000)
        if value < 1 or value > _MAX_SAFE_INTEGER:
            raise ShareSecurityNegotiationError("session_expiry_invalid")
        return value


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


_service: ShareSecurityNegotiationService | None = None


def get_share_security_negotiation_service() -> ShareSecurityNegotiationService:
    global _service
    if _service is None:
        signing_key = hashlib.sha256(
            b"ananta.share.security-negotiation.v1\0" + str(settings.secret_key).encode("utf-8")
        ).digest()
        _service = ShareSecurityNegotiationService(
            WebrtcSecurityPolicy(signing_key, allow_downgrade=False)
        )
    return _service


__all__ = [
    "ShareSecurityNegotiationError",
    "ShareSecurityNegotiationService",
    "get_share_security_negotiation_service",
]
