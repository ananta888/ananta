"""Execute exactly one immutable Hub assignment and bind its result."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.research_training import ResearchArtifactManifestV1, canonical_digest
from ananta_contracts.research_training_execution import ResearchStageAssignmentV1
from worker.training.research.backend import ResearchStageOutput
from worker.training.research.runtime_verifier import ResearchRuntimeVerifier


class BoundResearchBackend(Protocol):
    @property
    def capabilities(self) -> frozenset[str]: ...

    def execute(self, assignment: ResearchStageAssignmentV1) -> ResearchStageOutput: ...


class ResearchAssignmentRunner:
    def __init__(
        self,
        backend: BoundResearchBackend,
        *,
        runtime_verifier: ResearchRuntimeVerifier | None = None,
    ) -> None:
        self._backend = backend
        self._runtime_verifier = runtime_verifier

    def execute(self, raw_assignment: Mapping[str, Any]) -> dict[str, Any]:
        assignment = ResearchStageAssignmentV1.from_mapping(raw_assignment)
        if self._runtime_verifier is not None:
            self._runtime_verifier.configure_and_verify(assignment.runtime)
        if assignment.stage.required_capability not in self._backend.capabilities:
            raise PermissionError("research_worker_capability_missing")
        output = self._backend.execute(assignment)
        content_digest = hashlib.sha256(output.content).hexdigest()
        evidence_run_id = str(assignment.hub_evidence["run_id"])
        manifest = ResearchArtifactManifestV1.from_mapping(
            {
                "schema": ResearchArtifactManifestV1.SCHEMA,
                "tenant_id": assignment.run_spec.tenant_id,
                "run_id": assignment.run_id,
                "stage_id": assignment.stage.stage_id,
                "attempt_id": assignment.attempt_id,
                "artifact_kind": output.artifact_kind,
                "artifact_digest": content_digest,
                "size_bytes": len(output.content),
                "parent_artifact_digests": [item.artifact_digest for item in assignment.inputs],
                "recipe_digest": assignment.run_spec.recipe.digest,
                "dataset_digest": assignment.dataset_manifest.digest,
                "executable": output.executable,
                "source_refs": list(assignment.hub_evidence["source_ids"]),
                "run_refs": [evidence_run_id],
            }
        )
        result = {
            "schema": "ananta.research-training-worker-result.v2",
            "task_id": assignment.task_id,
            "assignment_id": assignment.assignment_id,
            "dispatch_lease_id": assignment.dispatch_lease_id,
            "attempt_id": assignment.attempt_id,
            "worker_id": assignment.worker_id,
            "run_id": assignment.run_id,
            "evidence_run_id": evidence_run_id,
            "stage_id": assignment.stage.stage_id,
            "assignment_digest": assignment.digest,
            "manifest": manifest.to_dict(),
            "content": output.content,
            "metrics": dict(output.metrics),
            "follow_up_stage_created": False,
            "human_intervention_required": False,
        }
        result["result_digest"] = canonical_digest(
            {key: value for key, value in result.items() if key not in {"content", "result_digest"}}
        )
        return result


__all__ = ["BoundResearchBackend", "ResearchAssignmentRunner"]
