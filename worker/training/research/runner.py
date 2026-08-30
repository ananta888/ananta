"""Validate and execute exactly one Hub-delegated research stage."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import (
    ResearchArtifactManifestV1,
    ResearchRunSpecV1,
    ResearchStageV1,
    ResearchTrainingContractError,
    require_id,
)
from worker.training.research.backend import ResearchWorkerBackend


class ResearchStageRunner:
    def __init__(self, backend: ResearchWorkerBackend) -> None:
        self._backend = backend

    def execute(
        self,
        *,
        run_spec: Mapping[str, Any],
        run_id: str,
        stage: Mapping[str, Any],
        attempt_id: str,
        parent_artifact_digests: Sequence[str] = (),
        source_refs: Sequence[str] = (),
        run_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        spec = ResearchRunSpecV1.from_mapping(run_spec)
        parsed_stage = ResearchStageV1.from_mapping(stage)
        expected_stage = next((item for item in spec.pipeline.stages if item.stage_id == parsed_stage.stage_id), None)
        if expected_stage != parsed_stage:
            raise ResearchTrainingContractError("research_worker_stage_binding_invalid")
        if parsed_stage.required_capability not in self._backend.capabilities:
            raise PermissionError("research_worker_capability_missing")
        attempt = require_id(attempt_id, "attempt_id")
        output = self._backend.execute(run_spec=spec.to_dict(), stage=parsed_stage.to_dict(), attempt_id=attempt)
        content_digest = hashlib.sha256(output.content).hexdigest()
        manifest = ResearchArtifactManifestV1.from_mapping({
            "schema": "ananta.research-training-artifact.v1",
            "tenant_id": spec.tenant_id,
            "run_id": require_id(run_id, "run_id"),
            "stage_id": parsed_stage.stage_id,
            "attempt_id": attempt,
            "artifact_kind": output.artifact_kind,
            "artifact_digest": content_digest,
            "size_bytes": len(output.content),
            "parent_artifact_digests": list(parent_artifact_digests),
            "recipe_digest": spec.recipe.digest,
            "dataset_digest": spec.dataset_manifest_digest,
            "executable": output.executable,
            "source_refs": list(source_refs),
            "run_refs": list(run_refs),
        })
        return {
            "schema": "ananta.research-training-worker-result.v1",
            "stage_id": parsed_stage.stage_id,
            "attempt_id": attempt,
            "manifest": manifest.to_dict(),
            "content": output.content,
            "metrics": dict(output.metrics),
            "follow_up_stage_created": False,
            "human_intervention_required": False,
        }


__all__ = ["ResearchStageRunner"]
