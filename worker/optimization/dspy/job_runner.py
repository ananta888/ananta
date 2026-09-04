"""Executes exactly one authorized optimization job without orchestration authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import monotonic
from typing import Any

from agent.services.dspy_optimization_ports import OptimizationEnginePort, ProgramArtifactPort
from ananta_contracts.dspy_optimization import OptimizationSpecV1, PromptProgramV1, require_id
from worker.optimization.dspy.attempt_context import DspyCheckpointStore, isolated_attempt


class DspyOptimizationJobRunner:
    def __init__(
        self,
        engine: OptimizationEnginePort,
        artifacts: ProgramArtifactPort,
        *,
        authorization_verifier: Callable[[Mapping[str, Any]], bool],
        clock: Callable[[], float] = monotonic,
        workspace_root: str | Path | None = None,
        checkpoints: DspyCheckpointStore | None = None,
    ) -> None:
        self._engine = engine
        self._artifacts = artifacts
        self._verify = authorization_verifier
        self._clock = clock
        self._workspace_root = Path(workspace_root or "/tmp/ananta-dspy-attempts")
        self._checkpoints = checkpoints

    def run(
        self,
        *,
        job: Mapping[str, Any],
        baseline: PromptProgramV1,
        records: Sequence[Mapping[str, Any]],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        if not self._verify(job):
            return _failed("dspy_worker_authorization_invalid")
        try:
            spec = OptimizationSpecV1.from_mapping(dict(job.get("spec") or {}))
            if job.get("tenant_id") != spec.tenant_id or baseline.tenant_id != spec.tenant_id:
                raise PermissionError("dspy_worker_tenant_mismatch")
            if len(records) > spec.budgets.max_dataset_records:
                raise ValueError("dspy_worker_dataset_limit_exceeded")
            if cancelled():
                return _cancelled()
            run_id = require_id(str(job["run_id"]), "run_id")
            checkpoint = (
                self._checkpoints.load(tenant_id=spec.tenant_id, run_id=run_id, spec_digest=spec.digest)
                if self._checkpoints
                else None
            )
            if self._checkpoints:
                self._checkpoints.put(
                    tenant_id=spec.tenant_id,
                    run_id=run_id,
                    spec_digest=spec.digest,
                    state={"phase": "admitted"},
                )
            with isolated_attempt(
                tenant_id=spec.tenant_id,
                run_id=run_id,
                spec_digest=spec.digest,
                workspace_root=self._workspace_root,
                checkpoint=checkpoint,
            ):
                started = self._clock()
                candidate = self._engine.optimize(spec, baseline, records)
                if self._clock() - started > spec.budgets.timeout_seconds:
                    return _timed_out()
                if cancelled():
                    return _cancelled()
                if self._checkpoints:
                    self._checkpoints.put(
                        tenant_id=spec.tenant_id,
                        run_id=run_id,
                        spec_digest=spec.digest,
                        state={"phase": "optimized", "program_digest": candidate.digest},
                    )
                artifact = dict(self._artifacts.put(tenant_id=spec.tenant_id, run_id=run_id, program=candidate))
                if int(artifact.get("size_bytes") or 0) > spec.budgets.max_artifact_bytes:
                    raise ValueError("dspy_worker_artifact_limit_exceeded")
            if self._checkpoints:
                self._checkpoints.discard(tenant_id=spec.tenant_id, run_id=run_id)
        except (KeyError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            reason = str(exc) if str(exc).startswith("dspy_") else "dspy_worker_execution_failed"
            return _failed(reason)
        return {
            "state": "completed",
            "reason_code": "dspy_worker_completed",
            "artifact": artifact,
            "program_digest": candidate.digest,
            "hub_task_created": False,
            "worker_delegation_performed": False,
            "human_intervention_required": False,
        }


def _failed(reason: str) -> dict[str, Any]:
    return {
        "state": "failed",
        "reason_code": reason,
        "artifact": None,
        "hub_task_created": False,
        "worker_delegation_performed": False,
        "human_intervention_required": False,
    }


def _cancelled() -> dict[str, Any]:
    return {
        "state": "cancelled",
        "reason_code": "dspy_worker_cancelled",
        "artifact": None,
        "hub_task_created": False,
        "worker_delegation_performed": False,
        "human_intervention_required": False,
    }


def _timed_out() -> dict[str, Any]:
    return {
        "state": "failed",
        "reason_code": "dspy_worker_timed_out",
        "artifact": None,
        "hub_task_created": False,
        "worker_delegation_performed": False,
        "human_intervention_required": False,
    }


__all__ = ["DspyOptimizationJobRunner"]
