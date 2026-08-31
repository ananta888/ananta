"""Executes one assignment only and never creates Hub or Worker work."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.services.dendritic_memory_ports import DendriticExperimentBackendPort, DendriticPackArtifactPort
from ananta_contracts.dendritic_memory import DendriticMemoryPackManifestV1, canonical_digest
from ananta_contracts.dendritic_memory_worker import (
    DendriticCheckpointV1,
    DendriticWorkerAssignmentV1,
    DendriticWorkerResultV1,
)


class DendriticMemoryJobRunner:
    def __init__(
        self,
        backend: DendriticExperimentBackendPort,
        artifacts: DendriticPackArtifactPort,
        *,
        authorization_verifier: Callable[[Mapping[str, Any]], bool],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._backend = backend
        self._artifacts = artifacts
        self._verify = authorization_verifier
        self._clock = clock

    def run(
        self,
        *,
        job: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        packs: Sequence[Mapping[str, Any]] = (),
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        assignment = DendriticWorkerAssignmentV1.from_mapping(job)
        if assignment.deadline_epoch_ms <= int(self._clock() * 1000):
            return _terminal(assignment, "failed", "dendritic_worker_deadline_exceeded")
        if not self._verify(job):
            return _terminal(assignment, "failed", "dendritic_worker_authorization_invalid")
        try:
            spec = assignment.spec
            if cancelled():
                return _terminal(assignment, "cancelled", "dendritic_worker_cancelled")
            parsed_packs = tuple(DendriticMemoryPackManifestV1.from_mapping(item) for item in packs)
            if any(
                item.tenant_id != spec.tenant_id
                or item.base_model_id != spec.base_model_id
                or item.base_model_snapshot_digest != spec.base_model_snapshot_digest
                for item in parsed_packs
            ):
                raise PermissionError("dendritic_worker_pack_binding_invalid")
            if spec.job_type == "train_dendritic_memory":
                if parsed_packs:
                    raise ValueError("dendritic_worker_train_packs_forbidden")
                output = self._backend.train(spec, records)
                result = self._training_result(assignment, output)
            elif spec.job_type == "evaluate_dendritic_memory":
                if len(parsed_packs) != 1 or records:
                    raise ValueError("dendritic_worker_evaluation_inputs_invalid")
                result = self._report_result(
                    assignment,
                    self._backend.evaluate(spec, parsed_packs[0]),
                    "dendritic_worker_evaluation_completed",
                )
            else:
                if tuple(item.digest for item in parsed_packs) != spec.parent_pack_digests or records:
                    raise ValueError("dendritic_worker_composition_inputs_invalid")
                result = self._report_result(
                    assignment,
                    self._backend.compose(spec, parsed_packs),
                    "dendritic_worker_composition_completed",
                )
            if cancelled():
                return _terminal(assignment, "cancelled", "dendritic_worker_cancelled")
        except (KeyError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            reason = str(exc) if str(exc).startswith("dendritic_") else "dendritic_worker_execution_failed"
            return _terminal(assignment, "failed", reason)
        return result

    def _training_result(
        self, assignment: DendriticWorkerAssignmentV1, output: Mapping[str, Any]
    ) -> dict[str, Any]:
        manifest = DendriticMemoryPackManifestV1.from_mapping(output["manifest"])
        if manifest.tenant_id != assignment.spec.tenant_id:
            raise PermissionError("dendritic_worker_manifest_tenant_mismatch")
        artifact = self._artifacts.put(manifest=manifest, files=output["files"])
        events = list(output.get("events") or ())
        if len(events) > 100_000:
            raise ValueError("dendritic_worker_event_limit_exceeded")
        checkpoint = _checkpoint(assignment, events)
        return DendriticWorkerResultV1(
            run_id=assignment.run_id,
            attempt_id=assignment.attempt_id,
            fencing_token=assignment.fencing_token,
            state="completed",
            reason_code="dendritic_worker_completed",
            event_count=len(events),
            artifact=dict(artifact),
            manifest=manifest.to_dict(),
            checkpoint=checkpoint,
        ).to_dict()

    @staticmethod
    def _report_result(
        assignment: DendriticWorkerAssignmentV1,
        output: Mapping[str, Any],
        reason_code: str,
    ) -> dict[str, Any]:
        if len(canonical_digest(output)) != 64:
            raise ValueError("dendritic_worker_report_invalid")
        return DendriticWorkerResultV1(
            run_id=assignment.run_id,
            attempt_id=assignment.attempt_id,
            fencing_token=assignment.fencing_token,
            state="completed",
            reason_code=reason_code,
            event_count=0,
            output={**dict(output), "report_digest": canonical_digest(output)},
        ).to_dict()


def _checkpoint(
    assignment: DendriticWorkerAssignmentV1,
    events: Sequence[Mapping[str, Any]],
) -> DendriticCheckpointV1 | None:
    candidate = next(
        (item for item in reversed(events) if item.get("type") == "checkpoint"),
        None,
    )
    if candidate is None:
        return None
    return DendriticCheckpointV1(
        tenant_id=assignment.spec.tenant_id,
        run_id=assignment.run_id,
        attempt_id=assignment.attempt_id,
        fencing_token=assignment.fencing_token,
        spec_digest=assignment.spec.digest,
        base_model_snapshot_digest=assignment.spec.base_model_snapshot_digest,
        configuration_digest=canonical_digest(assignment.spec.configuration.to_dict()),
        step=int(candidate.get("step") or 0),
        payload_digest=str(candidate.get("sha256") or ""),
    )


def _terminal(
    assignment: DendriticWorkerAssignmentV1,
    state: str,
    reason: str,
) -> dict[str, Any]:
    return DendriticWorkerResultV1(
        run_id=assignment.run_id,
        attempt_id=assignment.attempt_id,
        fencing_token=assignment.fencing_token,
        state=state,
        reason_code=reason,
        event_count=0,
    ).to_dict()


__all__ = ["DendriticMemoryJobRunner"]
