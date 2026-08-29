"""Executes exactly one authorized optimization job without orchestration authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.services.dspy_optimization_ports import OptimizationEnginePort, ProgramArtifactPort
from ananta_contracts.dspy_optimization import OptimizationSpecV1, PromptProgramV1


class DspyOptimizationJobRunner:
    def __init__(
        self,
        engine: OptimizationEnginePort,
        artifacts: ProgramArtifactPort,
        *,
        authorization_verifier: Callable[[Mapping[str, Any]], bool],
    ) -> None:
        self._engine = engine
        self._artifacts = artifacts
        self._verify = authorization_verifier

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
            candidate = self._engine.optimize(spec, baseline, records)
            if cancelled():
                return _cancelled()
            artifact = dict(self._artifacts.put(tenant_id=spec.tenant_id, run_id=str(job["run_id"]), program=candidate))
            if int(artifact.get("size_bytes") or 0) > spec.budgets.max_artifact_bytes:
                raise ValueError("dspy_worker_artifact_limit_exceeded")
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


__all__ = ["DspyOptimizationJobRunner"]
