"""Approval checks for VACGE graph mutations.

The graph editor must not treat an arbitrary string as approval.  Approval
tokens are bound to the canonical patch payload, the resolved risk tier and a
server-side secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConfigGraphApprovalDecision:
    approved: bool
    reason_code: str
    details: dict[str, Any] = field(default_factory=dict)


class ConfigGraphApprovalService:
    """Validates digest-bound approval tokens for graph patches."""

    def __init__(self, *, secret: str | None = None) -> None:
        self._secret = secret or os.environ.get("ANANTA_VACGE_APPROVAL_SECRET", "")

    def expected_token(
        self,
        *,
        ops: list[dict[str, Any]],
        risk_tier: str,
    ) -> str | None:
        if not self._secret:
            return None
        digest = self._digest(ops=ops, risk_tier=risk_tier)
        return f"vacge:{risk_tier}:{digest}"

    def validate(
        self,
        *,
        ops: list[dict[str, Any]],
        risk_tier: str,
        approval_token: str,
    ) -> ConfigGraphApprovalDecision:
        expected = self.expected_token(ops=ops, risk_tier=risk_tier)
        if expected is None:
            return ConfigGraphApprovalDecision(
                approved=False,
                reason_code="approval_secret_not_configured",
            )
        if not approval_token:
            return ConfigGraphApprovalDecision(
                approved=False,
                reason_code="approval_token_missing",
                details={"token_format": "vacge:<risk>:<digest>"},
            )
        if not hmac.compare_digest(approval_token, expected):
            return ConfigGraphApprovalDecision(
                approved=False,
                reason_code="approval_token_digest_mismatch",
                details={"token_format": "vacge:<risk>:<digest>"},
            )
        return ConfigGraphApprovalDecision(
            approved=True,
            reason_code="approval_token_valid",
            details={"digest_prefix": expected.rsplit(":", 1)[-1][:12]},
        )

    def _digest(self, *, ops: list[dict[str, Any]], risk_tier: str) -> str:
        payload = json.dumps(
            {"ops": ops, "risk_tier": risk_tier},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hmac.new(
            self._secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
