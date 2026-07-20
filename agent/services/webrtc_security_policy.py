"""Hub policy for downgrade consent and final security-contract activation."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from ananta_contracts.webrtc_security_negotiation import (
    SECURITY_MODE_STRENGTH,
    FinalSecurityContract,
    SecurityNegotiationError,
    SecurityProposal,
    security_contract_digest,
)


@dataclass
class DowngradeConsent:
    consent_id: str
    tenant_id: str
    user_id: str
    scope_id: str
    from_mode: str
    to_mode: str
    expires_at_ms: int
    visible_notice_acknowledged: bool
    revoked_at_ms: int | None = None


class WebrtcSecurityPolicy:
    def __init__(
        self,
        signing_key: bytes,
        *,
        allow_downgrade: bool = False,
        clock=time.time,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("security_policy_signing_key_too_short")
        self._key = bytes(signing_key)
        self._allow_downgrade = allow_downgrade
        self._clock = clock

    def finalize(
        self,
        *,
        offer: SecurityProposal,
        answer: SecurityProposal,
        authoritative_epoch: int,
        tenant_id: str,
        user_id: str,
        consent: DowngradeConsent | None = None,
    ) -> FinalSecurityContract:
        now_ms = int(self._clock() * 1000)
        if offer.negotiation_id != answer.negotiation_id or offer.scope_id != answer.scope_id:
            raise SecurityNegotiationError("negotiation_binding_mismatch")
        if offer.sender_id != answer.recipient_id or offer.recipient_id != answer.sender_id:
            raise SecurityNegotiationError("negotiation_peer_mismatch")
        if offer.key_epoch != authoritative_epoch or answer.key_epoch != authoritative_epoch:
            raise SecurityNegotiationError("epoch_mismatch")
        if offer.expires_at_ms <= now_ms or answer.expires_at_ms <= now_ms:
            raise SecurityNegotiationError("negotiation_expired")
        if offer.algorithms != answer.algorithms or offer.payload_classes != answer.payload_classes:
            raise SecurityNegotiationError("security_parameters_changed")
        required_strength = max(
            SECURITY_MODE_STRENGTH[offer.minimum_mode],
            SECURITY_MODE_STRENGTH[answer.minimum_mode],
        )
        selected_strength = min(
            SECURITY_MODE_STRENGTH[offer.selected_mode],
            SECURITY_MODE_STRENGTH[answer.selected_mode],
        )
        if selected_strength < required_strength:
            if not self._valid_consent(
                consent,
                tenant_id=tenant_id,
                user_id=user_id,
                scope_id=offer.scope_id,
                from_mode=offer.minimum_mode,
                to_mode=answer.selected_mode,
                now_ms=now_ms,
            ):
                raise SecurityNegotiationError("downgrade_consent_required")
        digest = security_contract_digest(offer, answer)
        signature = hmac.new(self._key, digest.encode("ascii"), hashlib.sha256).hexdigest()
        return FinalSecurityContract(offer=offer, answer=answer, digest=digest, signature=signature)

    def _valid_consent(
        self,
        consent: DowngradeConsent | None,
        *,
        tenant_id: str,
        user_id: str,
        scope_id: str,
        from_mode: str,
        to_mode: str,
        now_ms: int,
    ) -> bool:
        return bool(
            self._allow_downgrade
            and consent is not None
            and consent.visible_notice_acknowledged
            and consent.revoked_at_ms is None
            and consent.expires_at_ms > now_ms
            and consent.tenant_id == tenant_id
            and consent.user_id == user_id
            and consent.scope_id == scope_id
            and consent.from_mode == from_mode
            and consent.to_mode == to_mode
        )


__all__ = ["DowngradeConsent", "WebrtcSecurityPolicy"]
