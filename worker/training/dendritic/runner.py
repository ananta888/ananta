"""Executes one assignment only and never creates Hub or Worker work."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.services.dendritic_memory_ports import DendriticExperimentBackendPort, DendriticPackArtifactPort
from ananta_contracts.dendritic_memory import DendriticJobSpecV1, DendriticMemoryPackManifestV1


class DendriticMemoryJobRunner:
    def __init__(
        self,
        backend: DendriticExperimentBackendPort,
        artifacts: DendriticPackArtifactPort,
        *,
        authorization_verifier: Callable[[Mapping[str, Any]], bool],
    ) -> None:
        self._backend = backend
        self._artifacts = artifacts
        self._verify = authorization_verifier

    def run(
        self,
        *,
        job: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        if not self._verify(job):
            return _terminal("failed", "dendritic_worker_authorization_invalid")
        try:
            spec = DendriticJobSpecV1.from_mapping(dict(job.get("spec") or {}))
            if job.get("tenant_id") != spec.tenant_id:
                raise PermissionError("dendritic_worker_tenant_mismatch")
            if cancelled():
                return _terminal("cancelled", "dendritic_worker_cancelled")
            output = self._backend.train(spec, records)
            if cancelled():
                return _terminal("cancelled", "dendritic_worker_cancelled")
            manifest = DendriticMemoryPackManifestV1.from_mapping(output["manifest"])
            artifact = self._artifacts.put(manifest=manifest, files=output["files"])
        except (KeyError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            reason = str(exc) if str(exc).startswith("dendritic_") else "dendritic_worker_execution_failed"
            return _terminal("failed", reason)
        return {
            **_terminal("completed", "dendritic_worker_completed"),
            "artifact": dict(artifact),
            "manifest": manifest.to_dict(),
        }


def _terminal(state: str, reason: str) -> dict[str, Any]:
    return {
        "state": state,
        "reason_code": reason,
        "hub_task_created": False,
        "worker_delegation_performed": False,
        "human_intervention_required": False,
    }


__all__ = ["DendriticMemoryJobRunner"]
