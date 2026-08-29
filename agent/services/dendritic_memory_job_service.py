"""Hub-owned admission, attempt fencing and cancellation."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from agent.services.dendritic_memory_capability_service import DendriticMemoryCapabilityService
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_state_store import DendriticMemoryStateStore
from ananta_contracts.dendritic_memory import (
    DendriticJobSpecV1,
    DendriticRunState,
    canonical_json,
    require_id,
)


class DendriticMemoryDenied(RuntimeError):
    pass


class DendriticMemoryJobService:
    def __init__(
        self,
        store: DendriticMemoryStateStore,
        *,
        policy: DendriticMemoryPolicy,
        capabilities: DendriticMemoryCapabilityService,
        signing_key: bytes,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("dendritic_job_signing_key_too_short")
        self._store = store
        self._policy = policy
        self._capabilities = capabilities
        self._key = bytes(signing_key)

    def dry_run(self, *, spec: Mapping[str, Any]) -> dict[str, Any]:
        parsed = DendriticJobSpecV1.from_mapping(spec)
        reasons: list[str] = []
        try:
            self._policy.admit(parsed)
        except PermissionError as exc:
            reasons.append(str(exc))
        capability = self._capabilities.projection()
        if capability["state"] != "available" and self._policy.mode != "mock":
            reasons.append("dendritic_worker_unavailable")
        return {
            "admissible": not reasons,
            "reason_codes": reasons,
            "spec_digest": parsed.digest,
            "effective_configuration": parsed.configuration.to_dict(),
            "model_download_performed": False,
            "worker_call_performed": False,
            "human_intervention_required": False,
        }

    def create(self, *, spec: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        parsed = DendriticJobSpecV1.from_mapping(spec)
        dry_run = self.dry_run(spec=spec)
        if not dry_run["admissible"]:
            raise DendriticMemoryDenied(dry_run["reason_codes"][0])
        key = str(idempotency_key or "").strip()
        if not 8 <= len(key) <= 256 or any(character.isspace() for character in key):
            raise ValueError("dendritic_idempotency_key_invalid")
        run_id = f"dendritic-run-{uuid.uuid4().hex}"
        attempt_id = f"dendritic-attempt-{uuid.uuid4().hex}"
        authorization = self._authorization(parsed.tenant_id, run_id, attempt_id, parsed.digest)
        payload = {
            "tenant_id": parsed.tenant_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "state": DendriticRunState.QUEUED.value,
            "spec": parsed.to_dict(),
            "spec_digest": parsed.digest,
            "worker_authorization": authorization,
            "created_at": _now(),
            "updated_at": _now(),
            "reason_code": "dendritic_job_queued",
            "result": None,
            "experimental": True,
            "not_production_ready": True,
            "claims_not_verified": True,
            "human_intervention_required": False,
        }
        digest = hashlib.sha256(f"{parsed.tenant_id}\0{idempotency_key}".encode()).hexdigest()
        created, replayed = self._store.create(payload, idempotency_digest=digest)
        return {**created, "replayed": replayed}

    def transition(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        worker_authorization: str,
        target_state: str,
        expected_revision: int,
        reason_code: str,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        self._verify(current, attempt_id, worker_authorization)
        source = DendriticRunState(current["state"])
        target = DendriticRunState(target_state)
        source.assert_transition(target)
        if target == DendriticRunState.COMPLETED and not result:
            raise ValueError("dendritic_completed_result_required")
        payload = {
            **current,
            "state": target.value,
            "reason_code": require_id(reason_code, "reason_code"),
            "result": dict(result) if result else current.get("result"),
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def cancel(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        source = DendriticRunState(current["state"])
        target = (
            DendriticRunState.CANCELLED
            if source == DendriticRunState.QUEUED
            else DendriticRunState.CANCEL_REQUESTED
        )
        source.assert_transition(target)
        payload = {
            **current,
            "state": target.value,
            "reason_code": "dendritic_cancelled_by_hub_policy",
            "updated_at": _now(),
            "human_intervention_required": False,
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def get(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        return self._store.get(tenant_id, run_id)

    def list(self, *, tenant_id: str, limit: int = 100) -> dict[str, Any]:
        return {"items": self._store.list(tenant_id, limit=limit), "limit": limit}

    def _authorization(self, tenant_id: str, run_id: str, attempt_id: str, spec_digest: str) -> str:
        return hmac.new(
            self._key, canonical_json([tenant_id, run_id, attempt_id, spec_digest]).encode(), hashlib.sha256
        ).hexdigest()

    def _verify(self, current: Mapping[str, Any], attempt_id: str, authorization: str) -> None:
        if current.get("attempt_id") != attempt_id:
            raise DendriticMemoryDenied("dendritic_attempt_stale")
        expected = self._authorization(
            str(current["tenant_id"]), str(current["run_id"]), attempt_id, str(current["spec_digest"])
        )
        if not hmac.compare_digest(expected, str(authorization)):
            raise DendriticMemoryDenied("dendritic_worker_authorization_invalid")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["DendriticMemoryDenied", "DendriticMemoryJobService"]
