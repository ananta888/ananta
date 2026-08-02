from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from agent.config import settings


class WorkerResultCapabilityError(ValueError):
    pass


class WorkerResultCapabilityService:
    """Issue narrow, expiring result credentials without sharing a Hub bearer."""

    _SCHEMA = "worker_result_capability.v1"

    def __init__(self, *, signing_secret: str | None = None) -> None:
        self._signing_secret = str(signing_secret if signing_secret is not None else settings.secret_key or "")

    def issue(
        self,
        *,
        worker_id: str,
        source_task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        ttl_seconds: int = 900,
    ) -> str:
        if len(self._signing_secret) < 16:
            raise WorkerResultCapabilityError("worker_result_capability_signing_secret_unavailable")
        now = int(time.time())
        claims = {
            "schema": self._SCHEMA,
            "worker_id": str(worker_id or ""),
            "source_task_id": str(source_task_id or ""),
            "assignment_id": str(assignment_id or ""),
            "dispatch_lease_id": str(dispatch_lease_id or ""),
            "jti": secrets.token_urlsafe(24),
            "scopes": ["worker.result.submit", "worker.task_proposal.submit"],
            "iat": now,
            "exp": now + max(60, min(int(ttl_seconds), 3600)),
        }
        if any(not claims[field] for field in ("worker_id", "source_task_id", "assignment_id", "dispatch_lease_id")):
            raise WorkerResultCapabilityError("worker_result_capability_binding_required")
        payload = self._encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = self._signature(payload)
        return f"wrc1.{payload}.{signature}"

    def verify(
        self,
        token: str,
        *,
        source_task_id: str,
        assignment_id: str,
    ) -> dict[str, Any]:
        if len(self._signing_secret) < 16:
            raise WorkerResultCapabilityError("worker_result_capability_signing_secret_unavailable")
        parts = str(token or "").split(".")
        if len(parts) != 3 or parts[0] != "wrc1":
            raise WorkerResultCapabilityError("worker_result_capability_invalid")
        payload, signature = parts[1], parts[2]
        if not hmac.compare_digest(signature, self._signature(payload)):
            raise WorkerResultCapabilityError("worker_result_capability_signature_invalid")
        try:
            claims = json.loads(self._decode(payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkerResultCapabilityError("worker_result_capability_payload_invalid") from exc
        if str(claims.get("schema") or "") != self._SCHEMA:
            raise WorkerResultCapabilityError("worker_result_capability_schema_invalid")
        if int(claims.get("exp") or 0) < int(time.time()):
            raise WorkerResultCapabilityError("worker_result_capability_expired")
        if str(claims.get("source_task_id") or "") != str(source_task_id or ""):
            raise WorkerResultCapabilityError("worker_result_capability_task_mismatch")
        if str(claims.get("assignment_id") or "") != str(assignment_id or ""):
            raise WorkerResultCapabilityError("worker_result_capability_assignment_mismatch")
        if not 24 <= len(str(claims.get("jti") or "")) <= 128:
            raise WorkerResultCapabilityError("worker_result_capability_nonce_invalid")
        required = {"worker.result.submit", "worker.task_proposal.submit"}
        if not required.issubset({str(value) for value in list(claims.get("scopes") or [])}):
            raise WorkerResultCapabilityError("worker_result_capability_scope_invalid")
        return dict(claims)

    def _signature(self, payload: str) -> str:
        digest = hmac.new(
            self._signing_secret.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return self._encode(digest)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)


__all__ = ["WorkerResultCapabilityError", "WorkerResultCapabilityService"]
