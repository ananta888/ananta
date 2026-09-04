"""Hub-only lifecycle, fencing and cancellation for optimization jobs."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from agent.services.dspy_engine_capability_service import DspyEngineCapabilityService
from agent.services.dspy_observability_service import DspyOperationalTelemetry
from agent.services.dspy_optimization_policy import DspyOptimizationPolicy
from agent.services.dspy_optimization_state_store import DspyOptimizationStateStore
from ananta_contracts.dspy_optimization import OptimizationRunState, OptimizationSpecV1, canonical_json, require_id


class DspyOptimizationDenied(RuntimeError):
    pass


class DspyOptimizationJobService:
    def __init__(
        self,
        store: DspyOptimizationStateStore,
        *,
        policy: DspyOptimizationPolicy,
        capabilities: DspyEngineCapabilityService,
        signing_key: bytes,
        telemetry: DspyOperationalTelemetry | None = None,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("dspy_job_signing_key_too_short")
        self._store = store
        self._policy = policy
        self._capabilities = capabilities
        self._key = bytes(signing_key)
        self._telemetry = telemetry

    def dry_run(self, *, spec: Mapping[str, Any]) -> dict[str, Any]:
        parsed = OptimizationSpecV1.from_mapping(spec)
        capability = self._capabilities.projection()
        reasons: list[str] = []
        try:
            self._policy.admit(parsed)
        except PermissionError as exc:
            reasons.append(str(exc))
        if capability["state"] != "available" and self._policy.mode != "mock":
            reasons.append("dspy_worker_capability_unavailable")
        return {
            "admissible": not reasons,
            "reason_codes": reasons,
            "spec_digest": parsed.digest,
            "provider_roles": sorted(parsed.provider_bindings),
            "hard_limits": parsed.to_dict()["budgets"],
            "estimated_calls_upper_bound": parsed.budgets.max_model_calls,
            "model_call_performed": False,
            "human_intervention_required": False,
        }

    def create(self, *, spec: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        parsed = OptimizationSpecV1.from_mapping(spec)
        dry_run = self.dry_run(spec=spec)
        if not dry_run["admissible"]:
            raise DspyOptimizationDenied(dry_run["reason_codes"][0])
        run_id = f"dspy-run-{uuid.uuid4().hex}"
        attempt_id = f"dspy-attempt-{uuid.uuid4().hex}"
        authorization = self._authorization(parsed.tenant_id, run_id, attempt_id, parsed.digest)
        payload = {
            "tenant_id": parsed.tenant_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "state": OptimizationRunState.ADMITTED.value,
            "spec": parsed.to_dict(),
            "spec_digest": parsed.digest,
            "authorization": authorization,
            "created_at": _now(),
            "updated_at": _now(),
            "reason_code": "dspy_job_admitted",
            "artifact": None,
            "human_intervention_required": False,
        }
        created, replayed = self._store.create(payload, idempotency_key=idempotency_key)
        if not replayed:
            self._audit(created, action="created", actor_id="hub", correlation_id=idempotency_key)
        return {**created, "replayed": replayed}

    def worker_transition(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        authorization: str,
        target_state: str,
        expected_revision: int,
        reason_code: str,
        artifact: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        self._verify_authorization(current, attempt_id, authorization)
        source = OptimizationRunState(str(current["state"]))
        target = OptimizationRunState(target_state)
        source.assert_transition(target)
        if target == OptimizationRunState.COMPLETED and not artifact:
            raise ValueError("dspy_completed_artifact_required")
        payload = {
            **current,
            "state": target.value,
            "reason_code": require_id(reason_code, "reason_code"),
            "artifact": dict(artifact) if artifact else current.get("artifact"),
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        updated = self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)
        self._audit(updated, action=f"worker_{target.value}", actor_id="worker", correlation_id=attempt_id)
        return updated

    def cancel(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        source = OptimizationRunState(str(current["state"]))
        target = (
            OptimizationRunState.CANCELLED
            if source in {OptimizationRunState.REQUESTED, OptimizationRunState.ADMITTED}
            else OptimizationRunState.CANCELLING
        )
        source.assert_transition(target)
        payload = {
            **current,
            "state": target.value,
            "reason_code": "dspy_job_cancelled_by_policy",
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        updated = self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)
        self._audit(updated, action="cancelled", actor_id="hub", correlation_id=run_id)
        return updated

    def recover(self, *, tenant_id: str, timeout_before: str) -> dict[str, Any]:
        recovered: list[dict[str, Any]] = []
        for current in self._store.list(tenant_id, limit=100):
            state = OptimizationRunState(str(current["state"]))
            if state not in {
                OptimizationRunState.ADMITTED,
                OptimizationRunState.RUNNING,
                OptimizationRunState.CANCELLING,
            }:
                continue
            if str(current.get("updated_at") or "") >= timeout_before:
                continue
            payload = {
                **current,
                "state": OptimizationRunState.FAILED.value,
                "reason_code": "dspy_worker_lease_expired",
                "updated_at": _now(),
            }
            revision = int(payload.pop("revision"))
            updated = self._store.append(tenant_id, str(current["run_id"]), payload, expected_revision=revision)
            self._audit(updated, action="recovered", actor_id="hub", correlation_id=str(current["run_id"]))
            recovered.append(updated)
        return {"items": recovered, "count": len(recovered), "human_intervention_required": False}

    def get(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        return self._store.get(tenant_id, run_id)

    def list(self, *, tenant_id: str, limit: int = 100) -> dict[str, Any]:
        return {"items": self._store.list(tenant_id, limit=limit), "limit": limit}

    def _authorization(self, tenant_id: str, run_id: str, attempt_id: str, spec_digest: str) -> str:
        payload = canonical_json([tenant_id, run_id, attempt_id, spec_digest]).encode()
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def _verify_authorization(self, current: Mapping[str, Any], attempt_id: str, authorization: str) -> None:
        if current.get("attempt_id") != attempt_id:
            raise DspyOptimizationDenied("dspy_attempt_stale")
        expected = self._authorization(
            str(current["tenant_id"]), str(current["run_id"]), attempt_id, str(current["spec_digest"])
        )
        if not hmac.compare_digest(expected, str(authorization)):
            raise DspyOptimizationDenied("dspy_worker_authorization_invalid")

    def _audit(self, run: Mapping[str, Any], *, action: str, actor_id: str, correlation_id: str) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record_job(
            actor_id=actor_id,
            tenant_id=str(run["tenant_id"]),
            action=action,
            run_id=str(run["run_id"]),
            revision=int(run["revision"]),
            reason_code=str(run["reason_code"]),
            correlation_id=correlation_id,
            target_digest=str(run["spec_digest"]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["DspyOptimizationDenied", "DspyOptimizationJobService"]
