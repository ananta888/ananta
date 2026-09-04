"""Hub adapter from an existing scheduler claim to a closed Worker assignment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.research_training_assignment_store import ResearchTrainingAssignmentStore
from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
from agent.services.research_training_quota_service import ResearchTrainingQuotaService
from agent.services.research_training_run_service import ResearchTrainingRunService
from agent.services.research_training_safety_policy import ResearchTrainingSafetyPolicy
from agent.services.research_training_worker_registry import ResearchTrainingWorkerRegistry
from ananta_contracts.research_training import ResearchRunSpecV1
from ananta_contracts.research_training_data import ResearchDatasetManifestV1


class ResearchTrainingDispatchService:
    def __init__(
        self,
        *,
        runs: ResearchTrainingRunService,
        workers: ResearchTrainingWorkerRegistry,
        evidence: ResearchTrainingEvidenceService,
        assignments: ResearchTrainingAssignmentStore,
        safety: ResearchTrainingSafetyPolicy,
        quota: ResearchTrainingQuotaService,
    ) -> None:
        self._runs = runs
        self._workers = workers
        self._evidence = evidence
        self._assignments = assignments
        self._safety = safety
        self._quota = quota

    def prepare(
        self,
        *,
        tenant_id: str,
        run_id: str,
        project_id: str,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        expected_revision: int,
        dataset_manifest: Mapping[str, Any],
        runtime: Mapping[str, Any],
        inputs: Sequence[Mapping[str, Any]],
        parameters: Mapping[str, Any],
        workspace_subdir: str,
        required_storage_bytes: int,
        lease_seconds: int,
        evidence_scope: str,
        evidence_idempotency_key: str,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        run = self._runs.get(tenant_id=tenant_id, run_id=run_id)
        ready = sorted(
            (stage for stage in run["stages"].values() if stage["status"] == "ready"),
            key=lambda item: item["stage_id"],
        )
        if not ready:
            raise LookupError("research_stage_not_ready")
        stage = ready[0]
        recipe = run["spec"]["recipe"]
        parsed_dataset = ResearchDatasetManifestV1.from_mapping(dataset_manifest)
        if parsed_dataset.tenant_id != tenant_id or parsed_dataset.project_id != project_id:
            raise PermissionError("research_dataset_authority_binding_invalid")
        safety_reasons = self._safety.denial_reasons(
            spec=ResearchRunSpecV1.from_mapping(run["spec"]),
            dataset=parsed_dataset,
            existing_checkpoint_count=sum(
                bool(item.get("output_artifact_digest"))
                and item["kind"] in {"pretrain", "sft", "rl"}
                for item in run["stages"].values()
            ),
        )
        if safety_reasons:
            raise PermissionError(safety_reasons[0])
        worker = self._workers.select(
            required_capability=stage["required_capability"],
            world_size=int(recipe["world_size"]),
            precision=str(recipe["precision"]),
            required_storage_bytes=required_storage_bytes,
        )
        claim = self._runs.claim_next(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_id=worker["worker_id"],
            worker_inventory_digest=worker["report_digest"],
            lease_seconds=lease_seconds,
            expected_revision=expected_revision,
        )
        try:
            stage_state = claim["stages"][claim["claimed_stage_id"]]
            bound_parameters = dict(parameters)
            resume_step = stage_state.get("resume_optimizer_step")
            if resume_step is not None:
                supplied_digests = {str(item.get("artifact_digest") or "") for item in inputs}
                if str(stage_state.get("resume_checkpoint_digest") or "") not in supplied_digests:
                    raise ValueError("research_resume_checkpoint_input_missing")
                bound_parameters["resume_optimizer_step"] = int(resume_step)
            self._quota.reserve(
                tenant_id=tenant_id,
                reservation_id=assignment_id,
                expected_bytes=required_storage_bytes,
                lease_seconds=lease_seconds,
            )
            assignment = self._evidence.reserve_assignment(
                run=claim,
                stage_id=claim["claimed_stage_id"],
                task_id=task_id,
                assignment_id=assignment_id,
                dispatch_lease_id=dispatch_lease_id,
                dataset_manifest=dataset_manifest,
                runtime=runtime,
                inputs=inputs,
                parameters=bound_parameters,
                quota_reservation_id=assignment_id,
                workspace_subdir=workspace_subdir,
                idempotency_key=evidence_idempotency_key,
                evidence_scope=evidence_scope,
                synthetic=synthetic,
            )
            record = self._assignments.put(
                assignment,
                worker_authorization=claim["worker_authorization"],
            )
        except Exception:
            self._quota.release(tenant_id=tenant_id, reservation_id=assignment_id)
            self._runs.transition(
                tenant_id=tenant_id,
                run_id=run_id,
                stage_id=claim["claimed_stage_id"],
                attempt_id=claim["stages"][claim["claimed_stage_id"]]["attempt_id"],
                worker_authorization=claim["worker_authorization"],
                target="failed",
                expected_revision=claim["revision"],
                reason_code="research_assignment_preparation_failed",
                failure_class="transient_infrastructure",
            )
            raise
        return {
            "schema": "ananta.research-training-dispatch.v1",
            "worker_id": worker["worker_id"],
            "worker_inventory_digest": worker["report_digest"],
            "assignment": assignment,
            "assignment_digest": record["assignment_digest"],
            "run_revision": claim["revision"],
            "human_intervention_required": False,
        }


__all__ = ["ResearchTrainingDispatchService"]
