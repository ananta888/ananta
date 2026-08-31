"""Hub-owned admission, attempt fencing and cancellation."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from agent.services.dendritic_memory_capability_service import DendriticMemoryCapabilityService
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_state_store import (
    DendriticMemoryStateConflict,
    DendriticMemoryStateStore,
)
from ananta_contracts.dendritic_memory import (
    DendriticJobSpecV1,
    DendriticRunState,
    canonical_json,
    require_id,
)
from ananta_contracts.dendritic_memory_worker import (
    DendriticCheckpointV1,
    DendriticWorkerAssignmentV1,
    DendriticWorkerResultV1,
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
        fencing_token = 1
        deadline_epoch_ms = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
        tenant_scope_digest = hashlib.sha256(
            canonical_json([parsed.tenant_id, parsed.digest]).encode()
        ).hexdigest()
        authorization = self._authorization(
            parsed.tenant_id,
            run_id,
            attempt_id,
            parsed.digest,
            fencing_token,
            deadline_epoch_ms,
        )
        payload = {
            "tenant_id": parsed.tenant_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "fencing_token": fencing_token,
            "tenant_scope_digest": tenant_scope_digest,
            "correlation_id": f"dendritic-correlation-{uuid.uuid4().hex}",
            "deadline_epoch_ms": deadline_epoch_ms,
            "state": DendriticRunState.QUEUED.value,
            "spec": parsed.to_dict(),
            "spec_digest": parsed.digest,
            "worker_authorization": authorization,
            "created_at": _now(),
            "updated_at": _now(),
            "reason_code": "dendritic_job_queued",
            "result": None,
            "checkpoint": None,
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
        parsed_result = DendriticWorkerResultV1.from_mapping(result) if result else None
        if parsed_result is not None and (
            parsed_result.run_id != run_id
            or parsed_result.attempt_id != attempt_id
            or parsed_result.fencing_token != int(current["fencing_token"])
            or parsed_result.state != target.value
        ):
            raise DendriticMemoryDenied("dendritic_worker_result_binding_invalid")
        payload = {
            **current,
            "state": target.value,
            "reason_code": require_id(reason_code, "reason_code"),
            "result": parsed_result.to_dict() if parsed_result else current.get("result"),
            "checkpoint": (
                parsed_result.checkpoint.to_dict()
                if parsed_result is not None and parsed_result.checkpoint is not None
                else current.get("checkpoint")
            ),
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def cancel(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        source = DendriticRunState(current["state"])
        target = (
            DendriticRunState.CANCELLED
            if source in {DendriticRunState.QUEUED, DendriticRunState.RETRY_QUEUED}
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

    def resume(
        self,
        *,
        tenant_id: str,
        run_id: str,
        expected_revision: int,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        source = DendriticRunState(current["state"])
        source.assert_transition(DendriticRunState.RETRY_QUEUED)
        parsed = DendriticCheckpointV1.from_mapping(checkpoint)
        spec = DendriticJobSpecV1.from_mapping(current["spec"])
        if (
            parsed.tenant_id != tenant_id
            or parsed.run_id != run_id
            or parsed.attempt_id != current["attempt_id"]
            or parsed.fencing_token != int(current["fencing_token"])
            or parsed.spec_digest != spec.digest
            or parsed.base_model_snapshot_digest != spec.base_model_snapshot_digest
            or parsed.configuration_digest != hashlib.sha256(
                canonical_json(spec.configuration.to_dict()).encode()
            ).hexdigest()
        ):
            raise DendriticMemoryDenied("dendritic_checkpoint_resume_binding_invalid")
        fencing_token = int(current["fencing_token"]) + 1
        attempt_id = f"dendritic-attempt-{uuid.uuid4().hex}"
        deadline_epoch_ms = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
        authorization = self._authorization(
            tenant_id,
            run_id,
            attempt_id,
            spec.digest,
            fencing_token,
            deadline_epoch_ms,
        )
        payload = {
            **current,
            "attempt_id": attempt_id,
            "fencing_token": fencing_token,
            "deadline_epoch_ms": deadline_epoch_ms,
            "worker_authorization": authorization,
            "checkpoint": parsed.to_dict(),
            "state": DendriticRunState.RETRY_QUEUED.value,
            "reason_code": "dendritic_resume_queued_by_hub",
            "result": None,
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def worker_assignment(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        if current["state"] not in {
            DendriticRunState.QUEUED.value,
            DendriticRunState.RETRY_QUEUED.value,
            DendriticRunState.RUNNING.value,
            DendriticRunState.CANCEL_REQUESTED.value,
        }:
            raise DendriticMemoryDenied("dendritic_worker_assignment_state_invalid")
        return DendriticWorkerAssignmentV1(
            run_id=str(current["run_id"]),
            attempt_id=str(current["attempt_id"]),
            fencing_token=int(current["fencing_token"]),
            tenant_scope_digest=str(current["tenant_scope_digest"]),
            correlation_id=str(current["correlation_id"]),
            deadline_epoch_ms=int(current["deadline_epoch_ms"]),
            worker_authorization=str(current["worker_authorization"]),
            spec=dict(current["spec"]),
            checkpoint=current.get("checkpoint"),
        ).to_dict()

    def claim_next(self, *, limit: int = 100) -> dict[str, Any]:
        """Atomically lease the next queued job to an authenticated worker."""
        if not 1 <= limit <= 100:
            raise ValueError("dendritic_worker_claim_limit_invalid")
        for current in self._store.list_reconcilable(limit=limit):
            if current["state"] not in {
                DendriticRunState.QUEUED.value,
                DendriticRunState.RETRY_QUEUED.value,
            }:
                continue
            if int(current["deadline_epoch_ms"]) <= int(datetime.now(timezone.utc).timestamp() * 1000):
                continue
            payload = {
                **current,
                "state": DendriticRunState.RUNNING.value,
                "reason_code": "dendritic_worker_claimed",
                "updated_at": _now(),
            }
            revision = int(payload.pop("revision"))
            try:
                claimed = self._store.append(
                    str(current["tenant_id"]),
                    str(current["run_id"]),
                    payload,
                    expected_revision=revision,
                )
            except DendriticMemoryStateConflict:
                continue
            return {
                "claimed": True,
                "assignment": self.worker_assignment(
                    tenant_id=str(claimed["tenant_id"]), run_id=str(claimed["run_id"])
                ),
                "expected_revision": claimed["revision"],
                "human_intervention_required": False,
            }
        return {"claimed": False, "human_intervention_required": False}

    def reconcile(self, *, stale_after_seconds: int = 300, limit: int = 1000) -> dict[str, Any]:
        """Fail or cancel stale jobs without depending on a human operator."""
        if not 1 <= stale_after_seconds <= 86_400:
            raise ValueError("dendritic_reconcile_stale_after_invalid")
        now = datetime.now(timezone.utc)
        counts = {"examined": 0, "failed": 0, "cancelled": 0, "unchanged": 0, "raced": 0}
        for current in self._store.list_reconcilable(limit=limit):
            counts["examined"] += 1
            state = DendriticRunState(current["state"])
            deadline_expired = int(current.get("deadline_epoch_ms") or 0) <= int(
                now.timestamp() * 1000
            )
            updated_at = datetime.fromisoformat(str(current["updated_at"]).replace("Z", "+00:00"))
            stale = now - updated_at >= timedelta(seconds=stale_after_seconds)
            if state in {DendriticRunState.QUEUED, DendriticRunState.RETRY_QUEUED}:
                target = DendriticRunState.FAILED if deadline_expired else None
            elif state == DendriticRunState.RUNNING:
                target = DendriticRunState.FAILED if stale or deadline_expired else None
            elif state == DendriticRunState.CANCEL_REQUESTED:
                target = DendriticRunState.CANCELLED if stale else None
            else:
                target = None
            if target is None:
                counts["unchanged"] += 1
                continue
            payload = {
                **current,
                "state": target.value,
                "reason_code": (
                    "dendritic_cancel_reconciled"
                    if target == DendriticRunState.CANCELLED
                    else "dendritic_worker_lease_expired"
                ),
                "updated_at": _now(),
                "human_intervention_required": False,
            }
            revision = int(payload.pop("revision"))
            try:
                self._store.append(
                    str(current["tenant_id"]),
                    str(current["run_id"]),
                    payload,
                    expected_revision=revision,
                )
            except DendriticMemoryStateConflict:
                counts["raced"] += 1
            else:
                counts[target.value] += 1
        return {
            "schema_version": "ananta.dendritic-memory-reconcile.v1",
            **counts,
            "human_intervention_required": False,
        }

    def _authorization(
        self,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        spec_digest: str,
        fencing_token: int,
        deadline_epoch_ms: int,
    ) -> str:
        return hmac.new(
            self._key,
            canonical_json(
                [
                    tenant_id,
                    run_id,
                    attempt_id,
                    spec_digest,
                    fencing_token,
                    deadline_epoch_ms,
                ]
            ).encode(),
            hashlib.sha256,
        ).hexdigest()

    def _verify(self, current: Mapping[str, Any], attempt_id: str, authorization: str) -> None:
        if current.get("attempt_id") != attempt_id:
            raise DendriticMemoryDenied("dendritic_attempt_stale")
        expected = self._authorization(
            str(current["tenant_id"]),
            str(current["run_id"]),
            attempt_id,
            str(current["spec_digest"]),
            int(current["fencing_token"]),
            int(current["deadline_epoch_ms"]),
        )
        if not hmac.compare_digest(expected, str(authorization)):
            raise DendriticMemoryDenied("dendritic_worker_authorization_invalid")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["DendriticMemoryDenied", "DendriticMemoryJobService"]
