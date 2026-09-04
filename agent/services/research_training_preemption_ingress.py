"""Hub ingestion of a Worker preemption checkpoint and retry transition."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.services.research_training_artifact_service import ResearchTrainingArtifactService
from agent.services.research_training_assignment_store import ResearchTrainingAssignmentStore
from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
from agent.services.research_training_lineage_service import ResearchTrainingLineageService
from agent.services.research_training_run_service import ResearchTrainingRunService
from ananta_contracts.research_training import ResearchArtifactManifestV1, canonical_digest


class ResearchTrainingPreemptionIngress:
    def __init__(
        self,
        root: str | Path,
        *,
        assignments: ResearchTrainingAssignmentStore,
        evidence: ResearchTrainingEvidenceService,
        artifacts: ResearchTrainingArtifactService,
        lineage: ResearchTrainingLineageService,
        runs: ResearchTrainingRunService,
        maximum_checkpoint_bytes: int,
    ) -> None:
        self._root = Path(root).resolve()
        self._assignments = assignments
        self._evidence = evidence
        self._artifacts = artifacts
        self._lineage = lineage
        self._runs = runs
        self._maximum = int(maximum_checkpoint_bytes)

    def accept(
        self,
        *,
        tenant_id: str,
        project_id: str,
        worker_id: str,
        assignment_id: str,
        result_ref: str,
    ) -> dict[str, Any]:
        record = self._assignments.get(tenant_id=tenant_id, assignment_id=assignment_id)
        assignment = record["assignment"]
        if assignment["worker_id"] != worker_id:
            raise PermissionError("research_preemption_worker_binding_invalid")
        envelope = json.loads(self._path(result_ref).read_text())
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schema", "assignment_id", "worker_id", "checkpoint", "human_intervention_required"}
            or envelope.get("schema") != "ananta.research-training-preemption-result.v1"
            or envelope.get("assignment_id") != assignment_id
            or envelope.get("worker_id") != worker_id
            or envelope.get("human_intervention_required") is not False
            or not isinstance(envelope.get("checkpoint"), dict)
        ):
            raise ValueError("research_preemption_envelope_invalid")
        checkpoint = envelope["checkpoint"]
        if set(checkpoint) != {
            "schema",
            "stage_id",
            "attempt_id",
            "optimizer_step",
            "checkpoint_ref",
            "checkpoint_digest",
            "size_bytes",
        }:
            raise ValueError("research_preemption_checkpoint_fields_invalid")
        content_path = self._path(f"checkpoints/{checkpoint['checkpoint_ref']}")
        content = content_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if (
            digest != checkpoint["checkpoint_digest"]
            or len(content) != checkpoint["size_bytes"]
            or len(content) > self._maximum
            or checkpoint["stage_id"] != assignment["stage"]["stage_id"]
            or checkpoint["attempt_id"] != assignment["attempt_id"]
            or not isinstance(checkpoint["optimizer_step"], int)
            or isinstance(checkpoint["optimizer_step"], bool)
            or checkpoint["optimizer_step"] < 1
        ):
            raise ValueError("research_preemption_checkpoint_binding_invalid")
        stage_kind = assignment["stage"]["kind"]
        artifact_kind = {"pretrain": "base_checkpoint", "sft": "sft_checkpoint", "rl": "rl_checkpoint"}.get(
            stage_kind
        )
        if artifact_kind is None:
            raise ValueError("research_preemption_stage_invalid")
        manifest = ResearchArtifactManifestV1.from_mapping(
            {
                "schema": ResearchArtifactManifestV1.SCHEMA,
                "tenant_id": tenant_id,
                "run_id": assignment["run_id"],
                "stage_id": assignment["stage"]["stage_id"],
                "attempt_id": assignment["attempt_id"],
                "artifact_kind": artifact_kind,
                "artifact_digest": digest,
                "size_bytes": len(content),
                "parent_artifact_digests": [item["artifact_digest"] for item in assignment["inputs"]],
                "recipe_digest": assignment["run_spec"]["recipe_digest"]
                if "recipe_digest" in assignment["run_spec"]
                else canonical_digest(assignment["run_spec"]["recipe"]),
                "dataset_digest": assignment["run_spec"]["dataset_manifest_digest"],
                "executable": False,
                "source_refs": assignment["hub_evidence"]["source_ids"],
                "run_refs": [assignment["hub_evidence"]["run_id"]],
            }
        )
        artifact = self._artifacts.publish(
            manifest=manifest.to_dict(),
            content=content,
            reservation_id=assignment["quota_reservation_id"],
            retention_class="checkpoint",
        )
        lineage = self._lineage.register(manifest=manifest.to_dict(), artifact_ref=artifact["artifact_ref"])
        evidence = self._evidence.record_preemption(
            project_id=project_id,
            assignment=assignment,
            checkpoint_digest=digest,
        )
        result_digest = str(evidence["result_digest"])
        self._assignments.accept(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
            result_digest=result_digest,
        )
        run = self._runs.get(tenant_id=tenant_id, run_id=assignment["run_id"])
        stage = run["stages"][assignment["stage"]["stage_id"]]
        if stage["status"] == "ready" and stage.get("resume_checkpoint_digest") == digest:
            transitioned = run
            replayed = True
        else:
            transitioned = self._runs.preempt(
                tenant_id=tenant_id,
                run_id=assignment["run_id"],
                stage_id=assignment["stage"]["stage_id"],
                attempt_id=assignment["attempt_id"],
                worker_authorization=self._assignments.worker_authorization(
                    tenant_id=tenant_id, assignment_id=assignment_id
                ),
                checkpoint_digest=digest,
                optimizer_step=checkpoint["optimizer_step"],
                expected_revision=run["revision"],
            )
            replayed = False
        return {
            "schema": "ananta.research-training-preemption-ingress.v1",
            "run": transitioned,
            "artifact": artifact,
            "lineage": lineage,
            "evidence": evidence,
            "replayed": replayed,
            "human_intervention_required": False,
        }

    def _path(self, relative_ref: str) -> Path:
        path = Path(relative_ref)
        target = (self._root / path).resolve()
        if (
            not relative_ref
            or path.is_absolute()
            or ".." in path.parts
            or self._root not in target.parents
            or not target.is_file()
            or target.is_symlink()
        ):
            raise PermissionError("research_preemption_ref_invalid")
        return target


__all__ = ["ResearchTrainingPreemptionIngress"]
