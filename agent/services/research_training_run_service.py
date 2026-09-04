"""Hub-owned research pipeline lifecycle and Worker attempt fencing."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from agent.services.research_training_capability_service import ResearchTrainingCapabilityService
from agent.services.research_training_policy import ResearchTrainingPolicy
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_state_store import ResearchTrainingStateStore
from ananta_contracts.research_training import (
    ResearchArtifactManifestV1,
    ResearchRunSpecV1,
    canonical_digest,
    canonical_json,
    require_digest,
    require_id,
)


class ResearchTrainingDenied(RuntimeError):
    pass


class ResearchTrainingRunService:
    def __init__(
        self,
        store: ResearchTrainingStateStore,
        *,
        policy: ResearchTrainingPolicy,
        capabilities: ResearchTrainingCapabilityService,
        recipes: ResearchTrainingRecipeService,
        signing_key: bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("research_signing_key_too_short")
        self._store = store
        self._policy = policy
        self._capabilities = capabilities
        self._recipes = recipes
        self._key = bytes(signing_key)
        self._clock = clock

    def dry_run(self, *, spec: Mapping[str, Any]) -> dict[str, Any]:
        parsed = ResearchRunSpecV1.from_mapping(spec)
        preflight = self._recipes.preflight(parsed)
        required = {stage.required_capability for stage in parsed.pipeline.stages}
        reasons = list(preflight["reason_codes"])
        if not self._capabilities.supports(required):
            reasons.append("research_worker_capabilities_missing")
        return {
            **preflight,
            "admissible": not reasons,
            "reason_codes": sorted(set(reasons)),
            "spec_digest": parsed.digest,
            "pipeline_digest": parsed.pipeline.digest,
            "recipe_digest": parsed.recipe.digest,
            "required_capabilities": sorted(required),
            "stage_order": [stage.stage_id for stage in parsed.pipeline.stages],
        }

    def create(self, *, spec: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        parsed = ResearchRunSpecV1.from_mapping(spec)
        key = require_id(idempotency_key, "idempotency_key")
        dry_run = self.dry_run(spec=parsed.to_dict())
        if not dry_run["admissible"]:
            raise ResearchTrainingDenied(str(dry_run["reason_codes"][0]))
        run_id = f"research-run-{uuid.uuid4()}"
        stages = {
            stage.stage_id: {
                "stage_id": stage.stage_id,
                "kind": stage.kind,
                "dependencies": list(stage.dependencies),
                "required_capability": stage.required_capability,
                "max_attempts": stage.max_attempts,
                "attempts": 0,
                "status": "ready" if not stage.dependencies else "pending",
                "attempt_id": None,
                "worker_id": None,
                "worker_inventory_digest": None,
                "lease_expires_at_epoch": None,
                "last_heartbeat_at_epoch": None,
                "resume_checkpoint_digest": None,
                "resume_optimizer_step": None,
                "failure_class": None,
                "output_artifact_digest": None,
                "reason_code": None,
            }
            for stage in parsed.pipeline.stages
        }
        now = _now()
        value = {
            "schema": "ananta.research-training-run-state.v1",
            "tenant_id": parsed.tenant_id,
            "run_id": run_id,
            "spec": parsed.to_dict(),
            "spec_digest": parsed.digest,
            "state": "queued",
            "stages": stages,
            "automatic_release_requested": parsed.pipeline.automatic_release,
            "automatic_release_eligible": False,
            "experimental": True,
            "not_production_ready": True,
            "claims_not_verified": True,
            "human_intervention_required": False,
            "cloned_from_run_id": None,
            "reason_code": "research_run_queued",
            "created_at": now,
            "updated_at": now,
        }
        digest = hashlib.sha256(f"{parsed.tenant_id}:{key}".encode()).hexdigest()
        created, replayed = self._store.create(value, idempotency_digest=digest)
        return {**created, "replayed": replayed}

    def claim_next(
        self,
        *,
        tenant_id: str,
        run_id: str,
        worker_id: str,
        expected_revision: int,
        worker_inventory_digest: str | None = None,
        lease_seconds: int = 300,
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        if current["state"] not in {"queued", "running"}:
            raise ResearchTrainingDenied("research_run_not_claimable")
        stages = {key: dict(value) for key, value in dict(current["stages"]).items()}
        ready = sorted(
            (stage for stage in stages.values() if stage["status"] == "ready"),
            key=lambda item: item["stage_id"],
        )
        if not ready:
            raise ResearchTrainingDenied("research_stage_not_ready")
        stage = ready[0]
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("research_stage_lease_invalid")
        inventory_digest = (
            require_digest(worker_inventory_digest, "worker_inventory_digest")
            if worker_inventory_digest is not None
            else None
        )
        attempt_id = f"research-attempt-{uuid.uuid4()}"
        now_epoch = float(self._clock())
        stage.update(
            {
                "status": "running",
                "attempts": int(stage["attempts"]) + 1,
                "attempt_id": attempt_id,
                "worker_id": require_id(worker_id, "worker_id"),
                "worker_inventory_digest": inventory_digest,
                "lease_expires_at_epoch": now_epoch + lease_seconds,
                "last_heartbeat_at_epoch": now_epoch,
                "failure_class": None,
                "reason_code": "research_stage_claimed",
            }
        )
        stages[stage["stage_id"]] = stage
        payload = {
            **current,
            "state": "running",
            "stages": stages,
            "reason_code": "research_stage_running",
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        saved = self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)
        return {
            **saved,
            "claimed_stage_id": stage["stage_id"],
            "worker_authorization": self._authorization(saved, stage["stage_id"], attempt_id),
        }

    def transition(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stage_id: str,
        attempt_id: str,
        worker_authorization: str,
        target: str,
        expected_revision: int,
        artifact_manifest: Mapping[str, Any] | None = None,
        reason_code: str | None = None,
        failure_class: str = "transient_infrastructure",
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        stages = {key: dict(value) for key, value in dict(current["stages"]).items()}
        stage_key = require_id(stage_id, "stage_id")
        if stage_key not in stages:
            raise KeyError("research_stage_not_found")
        stage = stages[stage_key]
        if stage["status"] != "running" or stage["attempt_id"] != attempt_id:
            raise ResearchTrainingDenied("research_stage_attempt_stale")
        expected = self._authorization(current, stage_key, attempt_id)
        if not hmac.compare_digest(expected, str(worker_authorization)):
            raise ResearchTrainingDenied("research_worker_authorization_invalid")
        if target not in {"completed", "failed"}:
            raise ValueError("research_stage_target_invalid")
        if target == "completed":
            if not isinstance(artifact_manifest, Mapping):
                raise ValueError("research_stage_artifact_required")
            manifest = ResearchArtifactManifestV1.from_mapping(artifact_manifest)
            expected_recipe_digest = canonical_digest(current["spec"]["recipe"])
            if (
                manifest.tenant_id != tenant_id
                or manifest.run_id != run_id
                or manifest.stage_id != stage_key
                or manifest.attempt_id != attempt_id
                or manifest.recipe_digest != expected_recipe_digest
                or manifest.dataset_digest != current["spec"]["dataset_manifest_digest"]
            ):
                raise ResearchTrainingDenied("research_stage_artifact_binding_invalid")
            stage.update(
                status="completed",
                output_artifact_digest=manifest.artifact_digest,
                lease_expires_at_epoch=None,
                last_heartbeat_at_epoch=None,
                reason_code="research_stage_completed",
            )
        else:
            if failure_class not in {"transient_infrastructure", "preempted", "deterministic_input"}:
                raise ValueError("research_stage_failure_class_invalid")
            retryable = (
                failure_class != "deterministic_input"
                and int(stage["attempts"]) < int(stage["max_attempts"])
            )
            stage.update(
                status="ready" if retryable else "failed",
                attempt_id=None if retryable else attempt_id,
                worker_id=None if retryable else stage["worker_id"],
                lease_expires_at_epoch=None,
                last_heartbeat_at_epoch=None,
                failure_class=failure_class,
                reason_code=require_id(reason_code or "research_stage_failed", "reason_code"),
            )
        stages[stage_key] = stage
        self._release_ready_stages(stages)
        terminal_failure = any(item["status"] == "failed" for item in stages.values())
        completed = all(item["status"] == "completed" for item in stages.values())
        state = "failed" if terminal_failure else ("completed" if completed else "running")
        automatic_release_eligible = bool(
            completed and current["automatic_release_requested"] and self._policy.automatic_release_enabled
        )
        payload = {
            **current,
            "state": state,
            "stages": stages,
            "automatic_release_eligible": automatic_release_eligible,
            "reason_code": f"research_run_{state}",
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def heartbeat(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stage_id: str,
        attempt_id: str,
        worker_authorization: str,
        expected_revision: int,
        lease_seconds: int = 300,
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        stages = {key: dict(value) for key, value in dict(current["stages"]).items()}
        stage = stages.get(require_id(stage_id, "stage_id"))
        if stage is None or stage["status"] != "running" or stage["attempt_id"] != attempt_id:
            raise ResearchTrainingDenied("research_stage_attempt_stale")
        if not hmac.compare_digest(
            self._authorization(current, stage_id, attempt_id), str(worker_authorization)
        ):
            raise ResearchTrainingDenied("research_worker_authorization_invalid")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("research_stage_lease_invalid")
        now_epoch = float(self._clock())
        if float(stage.get("lease_expires_at_epoch") or 0) <= now_epoch:
            raise ResearchTrainingDenied("research_stage_lease_expired")
        stage["last_heartbeat_at_epoch"] = now_epoch
        stage["lease_expires_at_epoch"] = now_epoch + lease_seconds
        payload = {**current, "stages": stages, "updated_at": _now()}
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def reconcile_expired(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        now_epoch = float(self._clock())
        stages = {key: dict(value) for key, value in dict(current["stages"]).items()}
        changed = False
        for stage in stages.values():
            if stage["status"] != "running" or float(stage.get("lease_expires_at_epoch") or 0) > now_epoch:
                continue
            retryable = int(stage["attempts"]) < int(stage["max_attempts"])
            stage.update(
                status="ready" if retryable else "failed",
                attempt_id=None if retryable else stage["attempt_id"],
                worker_id=None if retryable else stage["worker_id"],
                lease_expires_at_epoch=None,
                last_heartbeat_at_epoch=None,
                failure_class="transient_infrastructure",
                reason_code="research_stage_lease_expired",
            )
            changed = True
        if not changed:
            return {**current, "replayed": True}
        state = "failed" if any(item["status"] == "failed" for item in stages.values()) else "running"
        payload = {**current, "stages": stages, "state": state, "updated_at": _now()}
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def pause(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        if current["state"] not in {"queued", "running"}:
            raise ResearchTrainingDenied("research_run_not_pausable")
        if any(stage["status"] == "running" for stage in current["stages"].values()):
            raise ResearchTrainingDenied("research_run_active_attempt_present")
        payload = {**current, "state": "paused", "reason_code": "research_run_paused", "updated_at": _now()}
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def resume(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        if current["state"] != "paused":
            raise ResearchTrainingDenied("research_run_not_paused")
        payload = {**current, "state": "queued", "reason_code": "research_run_resumed", "updated_at": _now()}
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def preempt(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stage_id: str,
        attempt_id: str,
        worker_authorization: str,
        checkpoint_digest: str,
        optimizer_step: int,
        expected_revision: int,
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        stages = {key: dict(value) for key, value in dict(current["stages"]).items()}
        stage = stages.get(require_id(stage_id, "stage_id"))
        if stage is None or stage["status"] != "running" or stage["attempt_id"] != attempt_id:
            raise ResearchTrainingDenied("research_stage_attempt_stale")
        if not hmac.compare_digest(
            self._authorization(current, stage_id, attempt_id), str(worker_authorization)
        ):
            raise ResearchTrainingDenied("research_worker_authorization_invalid")
        if not isinstance(optimizer_step, int) or isinstance(optimizer_step, bool) or optimizer_step < 1:
            raise ValueError("research_preemption_optimizer_step_invalid")
        stage.update(
            status="ready",
            attempt_id=None,
            worker_id=None,
            lease_expires_at_epoch=None,
            last_heartbeat_at_epoch=None,
            resume_checkpoint_digest=require_digest(checkpoint_digest, "checkpoint_digest"),
            resume_optimizer_step=optimizer_step,
            failure_class="preempted",
            reason_code="research_stage_preempted",
        )
        payload = {**current, "stages": stages, "updated_at": _now()}
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def resume_from_stage(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stage_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        if current["state"] not in {"completed", "failed", "paused"}:
            raise ResearchTrainingDenied("research_run_not_resettable")
        stages = {key: dict(value) for key, value in dict(current["stages"]).items()}
        target = require_id(stage_id, "stage_id")
        if target not in stages:
            raise KeyError("research_stage_not_found")
        reset = {target}
        changed = True
        while changed:
            before = len(reset)
            reset.update(
                key
                for key, stage in stages.items()
                if set(stage["dependencies"]) & reset
            )
            changed = len(reset) != before
        completed = {
            key for key, stage in stages.items() if key not in reset and stage["status"] == "completed"
        }
        for key in reset:
            stage = stages[key]
            stage.update(
                status="ready" if set(stage["dependencies"]) <= completed else "pending",
                attempts=0,
                attempt_id=None,
                worker_id=None,
                worker_inventory_digest=None,
                lease_expires_at_epoch=None,
                last_heartbeat_at_epoch=None,
                output_artifact_digest=None,
                resume_checkpoint_digest=None,
                resume_optimizer_step=None,
                reason_code="research_stage_reset",
                failure_class=None,
            )
        payload = {
            **current,
            "state": "queued",
            "stages": stages,
            "automatic_release_eligible": False,
            "reason_code": "research_run_resumed_from_stage",
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def clone(
        self,
        *,
        tenant_id: str,
        run_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        source = self._store.get(tenant_id, run_id)
        cloned = self.create(spec=source["spec"], idempotency_key=idempotency_key)
        if cloned["replayed"]:
            return cloned
        payload = {
            **cloned,
            "cloned_from_run_id": source["run_id"],
            "reason_code": "research_run_cloned",
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        payload.pop("replayed", None)
        saved = self._store.append(
            tenant_id,
            str(cloned["run_id"]),
            payload,
            expected_revision=int(cloned["revision"]),
        )
        return {**saved, "replayed": False}

    def cancel(self, *, tenant_id: str, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self._store.get(tenant_id, run_id)
        if current["state"] in {"completed", "failed", "cancelled"}:
            raise ResearchTrainingDenied("research_run_terminal")
        payload = {
            **current,
            "state": "cancelled",
            "reason_code": "research_run_cancelled_by_hub_policy",
            "updated_at": _now(),
        }
        payload.pop("revision", None)
        return self._store.append(tenant_id, run_id, payload, expected_revision=expected_revision)

    def get(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        return self._store.get(tenant_id, run_id)

    def list(self, *, tenant_id: str, limit: int = 100) -> dict[str, Any]:
        return self._store.list(tenant_id, limit=limit)

    @staticmethod
    def _release_ready_stages(stages: dict[str, dict[str, Any]]) -> None:
        completed = {stage_id for stage_id, stage in stages.items() if stage["status"] == "completed"}
        for stage in stages.values():
            if stage["status"] == "pending" and set(stage["dependencies"]) <= completed:
                stage["status"] = "ready"
                stage["reason_code"] = "research_stage_dependencies_completed"

    def _authorization(self, run: Mapping[str, Any], stage_id: str, attempt_id: str) -> str:
        inventory_digest = dict(run["stages"])[stage_id].get("worker_inventory_digest")
        payload = [
            run["tenant_id"],
            run["run_id"],
            stage_id,
            attempt_id,
            run["spec_digest"],
            inventory_digest,
        ]
        return hmac.new(self._key, canonical_json(payload).encode(), hashlib.sha256).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["ResearchTrainingDenied", "ResearchTrainingRunService"]
