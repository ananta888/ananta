"""Signed, scope-bound pagination cursors for the Organization planning read model."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from agent.services.organization_planning_errors import OrganizationPlanningCompositionError


class PlanningCursorCodec:
    _PREFIX = "opc1"

    def __init__(self, secret: str) -> None:
        self._secret = hashlib.sha256(str(secret or "").encode("utf-8")).digest()

    def encode(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        created_at: float,
        goal_id: str,
    ) -> str:
        claims = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "organization_id": organization_id,
            "created_at": float(created_at),
            "goal_id": goal_id,
        }
        payload = self._encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return f"{self._PREFIX}.{payload}.{self._signature(payload)}"

    def decode(
        self,
        cursor: str,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> tuple[float, str]:
        parts = str(cursor or "").split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            self._invalid()
        payload, signature = parts[1], parts[2]
        if not hmac.compare_digest(signature, self._signature(payload)):
            self._invalid()
        try:
            claims = json.loads(self._decode(payload).decode("utf-8"))
            created_at = float(claims["created_at"])
            goal_id = str(claims["goal_id"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._invalid()
        if (
            str(claims.get("tenant_id") or "") != tenant_id
            or str(claims.get("project_id") or "") != project_id
            or str(claims.get("organization_id") or "") != organization_id
            or not goal_id
        ):
            self._invalid()
        return created_at, goal_id

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
        return self._encode(digest)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def _invalid() -> None:
        raise OrganizationPlanningCompositionError(
            "organization_planning_cursor_invalid",
            status_code=400,
        )


__all__ = [
    "PlanningCursorCodec",
]
