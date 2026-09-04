"""Hub-owned result publication and terminal stage transition."""

from __future__ import annotations

from typing import Any

from agent.services.research_training_assignment_store import ResearchTrainingAssignmentStore
from agent.services.research_training_result_ingress import ResearchTrainingResultIngress
from agent.services.research_training_run_service import ResearchTrainingRunService


class ResearchTrainingCompletionService:
    def __init__(
        self,
        *,
        assignments: ResearchTrainingAssignmentStore,
        ingress: ResearchTrainingResultIngress,
        runs: ResearchTrainingRunService,
    ) -> None:
        self._assignments = assignments
        self._ingress = ingress
        self._runs = runs

    def complete(
        self,
        *,
        tenant_id: str,
        project_id: str,
        worker_id: str,
        assignment_id: str,
        result_ref: str,
        retention_class: str,
    ) -> dict[str, Any]:
        assignment_record = self._assignments.get(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
        )
        assignment = assignment_record["assignment"]
        ingress = self._ingress.accept(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
            worker_id=worker_id,
            result_ref=result_ref,
            project_id=project_id,
            reservation_id=assignment["quota_reservation_id"],
            retention_class=retention_class,
        )
        run = self._runs.get(tenant_id=tenant_id, run_id=assignment["run_id"])
        stage = dict(run["stages"])[assignment["stage"]["stage_id"]]
        if (
            stage["status"] == "completed"
            and stage["output_artifact_digest"] == ingress["manifest"]["artifact_digest"]
        ):
            return {
                "schema": "ananta.research-training-completion.v1",
                "assignment_id": assignment_id,
                "result_digest": ingress["result_digest"],
                "run": run,
                "artifact": ingress["artifact"],
                "evidence": ingress["evidence"],
                "human_intervention_required": False,
                "replayed": True,
            }
        if stage["attempt_id"] != assignment["attempt_id"]:
            raise ValueError("research_completion_attempt_binding_invalid")
        transitioned = self._runs.transition(
            tenant_id=tenant_id,
            run_id=assignment["run_id"],
            stage_id=assignment["stage"]["stage_id"],
            attempt_id=assignment["attempt_id"],
            worker_authorization=self._assignments.worker_authorization(
                tenant_id=tenant_id,
                assignment_id=assignment_id,
            ),
            target="completed",
            expected_revision=run["revision"],
            artifact_manifest=ingress["manifest"],
        )
        return {
            "schema": "ananta.research-training-completion.v1",
            "assignment_id": assignment_id,
            "result_digest": ingress["result_digest"],
            "run": transitioned,
            "artifact": ingress["artifact"],
            "evidence": ingress["evidence"],
            "human_intervention_required": False,
            "replayed": False,
        }


__all__ = ["ResearchTrainingCompletionService"]
