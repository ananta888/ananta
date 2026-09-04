"""Hub ingress for a closed Worker result-file envelope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.services.research_training_artifact_service import ResearchTrainingArtifactService
from agent.services.research_training_assignment_store import ResearchTrainingAssignmentStore
from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
from agent.services.research_training_lineage_service import ResearchTrainingLineageService


class ResearchTrainingResultIngress:
    def __init__(
        self,
        root: str | Path,
        *,
        evidence: ResearchTrainingEvidenceService,
        assignments: ResearchTrainingAssignmentStore,
        artifacts: ResearchTrainingArtifactService,
        lineage: ResearchTrainingLineageService,
        maximum_result_bytes: int,
    ) -> None:
        self._root = Path(root).resolve()
        self._evidence = evidence
        self._assignments = assignments
        self._artifacts = artifacts
        self._lineage = lineage
        self._maximum = int(maximum_result_bytes)

    def accept(
        self,
        *,
        tenant_id: str,
        assignment_id: str,
        worker_id: str,
        result_ref: str,
        project_id: str,
        reservation_id: str,
        retention_class: str,
    ) -> dict[str, Any]:
        assignment_record = self._assignments.get(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
        )
        assignment = assignment_record["assignment"]
        if assignment.get("worker_id") != worker_id:
            raise PermissionError("research_result_worker_binding_invalid")
        envelope_path = self._path(result_ref)
        try:
            envelope = json.loads(envelope_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("research_result_envelope_invalid") from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schema", "result", "content_ref", "human_intervention_required"}
            or envelope.get("schema") != "ananta.research-training-worker-result-file.v1"
            or envelope.get("human_intervention_required") is not False
            or not isinstance(envelope.get("result"), dict)
        ):
            raise ValueError("research_result_envelope_invalid")
        content_path = self._path(str(envelope.get("content_ref") or ""))
        if content_path.stat().st_size > self._maximum:
            raise ValueError("research_result_content_too_large")
        content = content_path.read_bytes()
        result = {**envelope["result"], "content": content}
        artifact_receipt = self._artifacts.publish(
            manifest=result["manifest"],
            content=content,
            reservation_id=reservation_id,
            retention_class=retention_class,
        )
        lineage = self._lineage.register(
            manifest=result["manifest"], artifact_ref=artifact_receipt["artifact_ref"]
        )
        evidence_receipt = self._evidence.record_result(
            project_id=project_id,
            assignment=assignment,
            result=result,
            terminal_state="succeeded",
        )
        assignment_receipt = self._assignments.accept(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
            result_digest=result["result_digest"],
        )
        return {
            "schema": "ananta.research-training-result-ingress.v1",
            "evidence": evidence_receipt,
            "artifact": artifact_receipt,
            "lineage": lineage,
            "assignment": assignment_receipt,
            "manifest": result["manifest"],
            "result_digest": result["result_digest"],
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
            raise PermissionError("research_result_ref_invalid")
        return target


__all__ = ["ResearchTrainingResultIngress"]
