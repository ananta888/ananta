"""Hub-owned evidence admission and assignment reservation for research work."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort
from ananta_contracts.research_training import ResearchArtifactManifestV1, canonical_digest
from ananta_contracts.research_training_data import ResearchDatasetManifestV1
from ananta_contracts.research_training_execution import (
    ResearchArtifactInputV1,
    ResearchRuntimeManifestV1,
    ResearchStageAssignmentV1,
)


class ResearchTrainingEvidenceService:
    """Translate admitted facts into registry-issued identities.

    This is intentionally a Hub service.  It contains no fallback identifier
    generator and never accepts caller-assigned identities on the automatic
    path.
    """

    def __init__(self, registry: EvidenceIdentityRegistryPort) -> None:
        self._registry = registry

    def admit_source(
        self,
        *,
        tenant_id: str,
        project_id: str,
        origin_type: str,
        origin: Mapping[str, Any],
        content: bytes,
        policy: Mapping[str, Any],
        evidence_scope: str,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(content, bytes) or not content:
            raise ValueError("research_source_content_invalid")
        identity = self._registry.register_source(
            tenant_id=tenant_id,
            project_id=project_id,
            origin_type=origin_type,
            origin_digest=canonical_digest(origin),
            content_digest=hashlib.sha256(content).hexdigest(),
            policy_digest=canonical_digest(policy),
            evidence_scope=evidence_scope,  # type: ignore[arg-type]
            synthetic=synthetic,
        )
        return {
            "schema": "ananta.research-training-source-admission.v1",
            "source_id": identity.source_id,
            "binding_digest": identity.binding_digest,
            "content_digest": identity.content_digest,
            "evidence_scope": identity.evidence_scope,
            "synthetic": identity.synthetic,
        }

    def reserve_assignment(
        self,
        *,
        run: Mapping[str, Any],
        stage_id: str,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        dataset_manifest: Mapping[str, Any],
        runtime: Mapping[str, Any],
        inputs: Sequence[Mapping[str, Any]],
        parameters: Mapping[str, Any],
        quota_reservation_id: str,
        workspace_subdir: str,
        idempotency_key: str,
        evidence_scope: str,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        spec = dict(run.get("spec") or {})
        stages = dict(run.get("stages") or {})
        stage_state = dict(stages.get(stage_id) or {})
        if stage_state.get("status") != "running" or not stage_state.get("attempt_id"):
            raise ValueError("research_assignment_stage_not_claimed")
        pipeline_stages = list(dict(spec.get("pipeline") or {}).get("stages") or [])
        stage = next((item for item in pipeline_stages if item.get("stage_id") == stage_id), None)
        if not isinstance(stage, Mapping):
            raise ValueError("research_assignment_stage_missing")
        dataset = ResearchDatasetManifestV1.from_mapping(dataset_manifest)
        runtime_manifest = ResearchRuntimeManifestV1.from_mapping(runtime)
        parsed_inputs = tuple(ResearchArtifactInputV1.from_mapping(item) for item in inputs)
        completed_outputs = {
            str(value["output_artifact_digest"])
            for value in stages.values()
            if value.get("status") == "completed" and value.get("output_artifact_digest")
        }
        resume_output = str(stage_state.get("resume_checkpoint_digest") or "")
        direct_outputs = {
            str(stages[dependency]["output_artifact_digest"])
            for dependency in stage.get("dependencies") or []
            if dependency in stages
            and stages[dependency].get("status") == "completed"
            and stages[dependency].get("output_artifact_digest")
        }
        supplied_outputs = {item.artifact_digest for item in parsed_inputs}
        allowed_outputs = completed_outputs | ({resume_output} if resume_output else set())
        if (
            not direct_outputs <= supplied_outputs
            or (resume_output and resume_output not in supplied_outputs)
            or not supplied_outputs <= allowed_outputs
        ):
            raise ValueError("research_assignment_parent_artifact_binding_invalid")
        input_digest = canonical_digest(
            {
                "run_spec": spec,
                "stage": dict(stage),
                "dataset_manifest": dataset.to_dict(),
                "inputs": [item.to_dict() for item in parsed_inputs],
                "parameters": dict(parameters),
            }
        )
        reserved = self._registry.reserve_run(
            tenant_id=str(run["tenant_id"]),
            project_id=dataset.project_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            repository_revision=runtime_manifest.repository_revision,
            input_digest=input_digest,
            execution_profile_digest=runtime_manifest.digest,
            environment_digest=canonical_digest(
                {
                    "image_digest": runtime_manifest.image_digest,
                    "hardware_profile_digest": runtime_manifest.hardware_profile_digest,
                    "python_version": runtime_manifest.python_version,
                    "torch_version": runtime_manifest.torch_version,
                    "cuda_version": runtime_manifest.cuda_version,
                }
            ),
            source_ids=dataset.source_ids,
            evidence_scope=evidence_scope,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            synthetic=synthetic,
        )
        evidence = self._registry.assignment_projection(
            tenant_id=str(run["tenant_id"]),
            project_id=dataset.project_id,
            run_id=reserved.run_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
        )
        assignment = ResearchStageAssignmentV1.from_mapping(
            {
                "schema": ResearchStageAssignmentV1.SCHEMA,
                "task_id": task_id,
                "assignment_id": assignment_id,
                "dispatch_lease_id": dispatch_lease_id,
                "attempt_id": stage_state["attempt_id"],
                "worker_id": stage_state["worker_id"],
                "quota_reservation_id": quota_reservation_id,
                "run_id": run["run_id"],
                "run_spec": spec,
                "stage": dict(stage),
                "dataset_manifest": dataset.to_dict(),
                "runtime": runtime_manifest.to_dict(),
                "inputs": [item.to_dict() for item in parsed_inputs],
                "parameters": dict(parameters),
                "workspace_subdir": workspace_subdir,
                "hub_evidence": evidence,
            }
        )
        return assignment.to_dict()

    def record_result(
        self,
        *,
        project_id: str,
        assignment: Mapping[str, Any],
        result: Mapping[str, Any],
        terminal_state: str,
    ) -> dict[str, Any]:
        parsed = ResearchStageAssignmentV1.from_mapping(assignment)
        expected_fields = {
            "schema",
            "task_id",
            "assignment_id",
            "dispatch_lease_id",
            "attempt_id",
            "worker_id",
            "run_id",
            "evidence_run_id",
            "stage_id",
            "assignment_digest",
            "manifest",
            "content",
            "metrics",
            "follow_up_stage_created",
            "human_intervention_required",
            "result_digest",
        }
        if set(result) != expected_fields or result.get("schema") != "ananta.research-training-worker-result.v2":
            raise ValueError("research_result_fields_invalid")
        if (
            result.get("assignment_digest") != parsed.digest
            or result.get("assignment_id") != parsed.assignment_id
            or result.get("dispatch_lease_id") != parsed.dispatch_lease_id
            or result.get("attempt_id") != parsed.attempt_id
            or result.get("worker_id") != parsed.worker_id
            or result.get("evidence_run_id") != parsed.hub_evidence["run_id"]
            or result.get("task_id") != parsed.task_id
            or result.get("run_id") != parsed.run_id
            or result.get("stage_id") != parsed.stage.stage_id
            or result.get("follow_up_stage_created") is not False
            or result.get("human_intervention_required") is not False
        ):
            raise ValueError("research_result_assignment_binding_invalid")
        raw_manifest = result.get("manifest")
        content = result.get("content")
        metrics = result.get("metrics")
        if not isinstance(raw_manifest, Mapping) or not isinstance(content, bytes) or not isinstance(metrics, Mapping):
            raise ValueError("research_result_payload_invalid")
        manifest = ResearchArtifactManifestV1.from_mapping(raw_manifest)
        if (
            manifest.tenant_id != parsed.run_spec.tenant_id
            or manifest.run_id != parsed.run_id
            or manifest.stage_id != parsed.stage.stage_id
            or manifest.attempt_id != parsed.attempt_id
            or manifest.artifact_digest != hashlib.sha256(content).hexdigest()
            or manifest.size_bytes != len(content)
            or manifest.source_refs != tuple(parsed.hub_evidence["source_ids"])
            or manifest.run_refs != (parsed.hub_evidence["run_id"],)
        ):
            raise ValueError("research_result_artifact_binding_invalid")
        result_digest = str(result.get("result_digest") or "")
        expected_digest = canonical_digest(
            {key: value for key, value in result.items() if key not in {"content", "result_digest"}}
        )
        if result_digest != expected_digest:
            raise ValueError("research_result_digest_invalid")
        recorded = self._registry.record_result(
            tenant_id=parsed.run_spec.tenant_id,
            project_id=project_id,
            run_id=str(parsed.hub_evidence["run_id"]),
            assignment_id=parsed.assignment_id,
            dispatch_lease_id=parsed.dispatch_lease_id,
            terminal_state=terminal_state,  # type: ignore[arg-type]
            result_digest=result_digest,
        )
        return {
            "schema": "ananta.research-training-evidence-result.v1",
            "run_id": recorded.run_id,
            "state": recorded.state,
            "result_digest": recorded.result_digest,
            "binding_digest": recorded.binding_digest,
        }

    def record_preemption(
        self,
        *,
        project_id: str,
        assignment: Mapping[str, Any],
        checkpoint_digest: str,
    ) -> dict[str, Any]:
        parsed = ResearchStageAssignmentV1.from_mapping(assignment)
        result_digest = canonical_digest(
            {
                "schema": "ananta.research-training-preemption-evidence.v1",
                "assignment_digest": parsed.digest,
                "checkpoint_digest": checkpoint_digest,
            }
        )
        recorded = self._registry.record_result(
            tenant_id=parsed.run_spec.tenant_id,
            project_id=project_id,
            run_id=str(parsed.hub_evidence["run_id"]),
            assignment_id=parsed.assignment_id,
            dispatch_lease_id=parsed.dispatch_lease_id,
            terminal_state="cancelled",
            result_digest=result_digest,
        )
        return {
            "schema": "ananta.research-training-evidence-result.v1",
            "run_id": recorded.run_id,
            "state": recorded.state,
            "result_digest": recorded.result_digest,
            "binding_digest": recorded.binding_digest,
        }


__all__ = ["ResearchTrainingEvidenceService"]
