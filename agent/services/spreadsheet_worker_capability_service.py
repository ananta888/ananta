"""Short-lived, assignment-bound capabilities for spreadsheet Workers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from agent.config import settings
from ananta_contracts.spreadsheet_studio import require_digest, require_id


class SpreadsheetWorkerCapabilityError(ValueError):
    pass


class SpreadsheetWorkerCapabilityService:
    _SCHEMA = "ananta.spreadsheet-worker-capability.v1"
    _SCOPES = frozenset({"spreadsheet.artifact.read", "spreadsheet.result.submit"})

    def __init__(self, *, signing_secret: str | None = None, clock=time.time) -> None:
        self._secret = str(signing_secret if signing_secret is not None else settings.secret_key or "")
        self._clock = clock

    def issue(
        self,
        *,
        scope: str,
        tenant_id: str,
        job: Mapping[str, Any],
        jti: str,
        ttl_seconds: int = 600,
    ) -> str:
        normalized_scope = str(scope or "")
        if normalized_scope not in self._SCOPES:
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_scope_invalid")
        if len(self._secret) < 16:
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_signing_secret_unavailable")
        now = int(self._clock())
        claims = {
            "schema": self._SCHEMA,
            "scope": normalized_scope,
            "tenant_id": require_id(tenant_id, "tenant_id"),
            "job_id": require_id(job.get("job_id"), "job_id"),
            "worker_job_id": require_id(job.get("worker_job_id"), "worker_job_id"),
            "slot_lease_id": require_id(job.get("slot_lease_id"), "slot_lease_id"),
            "worker_id": require_id(job.get("worker_id"), "worker_id"),
            "assignment_digest": require_digest(job.get("assignment_digest"), "assignment_digest"),
            "jti": require_id(jti, "capability_jti"),
            "iat": now,
            "exp": now + max(60, min(int(ttl_seconds), 900)),
        }
        payload = self._encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        return f"swc1.{payload}.{self._signature(payload)}"

    def verify(self, token: str, *, scope: str, job_id: str) -> dict[str, Any]:
        if len(self._secret) < 16:
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_signing_secret_unavailable")
        parts = str(token or "").split(".")
        if len(parts) != 3 or parts[0] != "swc1":
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_invalid")
        payload, supplied_signature = parts[1], parts[2]
        if not hmac.compare_digest(supplied_signature, self._signature(payload)):
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_signature_invalid")
        try:
            claims = json.loads(self._decode(payload).decode())
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_payload_invalid") from exc
        if claims.get("schema") != self._SCHEMA or claims.get("scope") != scope or claims.get("job_id") != job_id:
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_binding_invalid")
        if int(claims.get("exp") or 0) < int(self._clock()):
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_expired")
        try:
            for field in ("tenant_id", "worker_job_id", "slot_lease_id", "worker_id", "jti"):
                require_id(claims.get(field), field)
            require_digest(claims.get("assignment_digest"), "assignment_digest")
        except ValueError as exc:
            raise SpreadsheetWorkerCapabilityError("spreadsheet_capability_claims_invalid") from exc
        return dict(claims)

    def _signature(self, payload: str) -> str:
        return self._encode(hmac.new(self._secret.encode(), payload.encode("ascii"), hashlib.sha256).digest())

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = ["SpreadsheetWorkerCapabilityError", "SpreadsheetWorkerCapabilityService"]
