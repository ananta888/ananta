"""Authoritative, headless promotion decision for completed research runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort
from agent.services.research_training_evaluation_service import ResearchTrainingEvaluationService
from agent.services.research_training_run_service import ResearchTrainingRunService
from ananta_contracts.research_training import canonical_digest


class ResearchTrainingPromotionService:
    def __init__(
        self,
        *,
        runs: ResearchTrainingRunService,
        evaluations: ResearchTrainingEvaluationService,
        registry: EvidenceIdentityRegistryPort,
    ) -> None:
        self._runs = runs
        self._evaluations = evaluations
        self._registry = registry

    def decide(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        evaluation: Mapping[str, Any],
        quality_decision: Mapping[str, Any],
        evidence_bindings: Sequence[Mapping[str, Any]],
        required_scope: str,
    ) -> dict[str, Any]:
        run = self._runs.get(tenant_id=tenant_id, run_id=run_id)
        reasons: list[str] = []
        if run["state"] != "completed":
            reasons.append("research_run_not_completed")
        if run["automatic_release_eligible"] is not True:
            reasons.append("research_automatic_release_not_enabled")
        if not self._evaluations.verify(evaluation):
            reasons.append("research_evaluation_attestation_invalid")
        if evaluation.get("run_id") != run_id:
            reasons.append("research_evaluation_run_binding_invalid")
        if evaluation.get("dataset_manifest_digest") != run["spec"]["dataset_manifest_digest"]:
            reasons.append("research_evaluation_dataset_binding_invalid")
        if evaluation.get("release_eligible") is not True or evaluation.get("reason_codes"):
            reasons.append("research_evaluation_gate_failed")
        if (
            quality_decision.get("schema") != "ananta.research-training-quality-decision.v1"
            or quality_decision.get("eligible") is not True
            or quality_decision.get("reason_codes")
            or quality_decision.get("decision_digest")
            != canonical_digest({key: value for key, value in quality_decision.items() if key != "decision_digest"})
        ):
            reasons.append("research_quality_gate_failed")
        if required_scope not in {"local", "external", "production"}:
            reasons.append("research_release_scope_invalid")
        source_refs = tuple(sorted(str(item) for item in evaluation.get("source_refs") or []))
        run_refs = tuple(sorted(str(item) for item in evaluation.get("run_refs") or []))
        stage_outputs = {
            stage_id: str(stage.get("output_artifact_digest") or "")
            for stage_id, stage in sorted(dict(run.get("stages") or {}).items())
            if stage.get("status") == "completed" and stage.get("output_artifact_digest")
        }
        checkpoint_candidates = [
            str(stage.get("output_artifact_digest") or "")
            for stage in dict(run.get("stages") or {}).values()
            if stage.get("kind") in {"export", "rl", "sft", "pretrain"}
            and stage.get("status") == "completed"
            and stage.get("output_artifact_digest")
        ]
        if not checkpoint_candidates:
            reasons.append("research_promotable_checkpoint_missing")
        binding_runs: set[str] = set()
        binding_sources: set[str] = set()
        if not evidence_bindings:
            reasons.append("research_evidence_bindings_missing")
        for binding in evidence_bindings:
            if set(binding) != {"run_id", "task_id", "source_ids"}:
                reasons.append("research_evidence_binding_fields_invalid")
                continue
            source_ids = binding.get("source_ids")
            if not isinstance(source_ids, Sequence) or isinstance(source_ids, (str, bytes)):
                reasons.append("research_evidence_binding_sources_invalid")
                continue
            verification = self._registry.verify_release_binding(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=str(binding.get("run_id") or ""),
                required_scope=required_scope,  # type: ignore[arg-type]
                task_id=str(binding.get("task_id") or ""),
                repository_revision=str(run["spec"]["source_revision_digest"]),
                source_ids=[str(item) for item in source_ids],
            )
            if not verification.verified:
                reasons.append(verification.reason_code)
            binding_runs.add(str(binding.get("run_id") or ""))
            binding_sources.update(str(item) for item in source_ids)
        if tuple(sorted(binding_runs)) != run_refs:
            reasons.append("research_evaluation_run_refs_mismatch")
        if tuple(sorted(binding_sources)) != source_refs:
            reasons.append("research_evaluation_source_refs_mismatch")
        provenance = {
            "schema": "ananta.research-training-provenance.v1",
            "run_id": run_id,
            "spec_digest": run["spec_digest"],
            "repository_revision": run["spec"]["source_revision_digest"],
            "dataset_manifest_digest": run["spec"]["dataset_manifest_digest"],
            "recipe_digest": canonical_digest(run["spec"]["recipe"]),
            "pipeline_digest": canonical_digest(run["spec"]["pipeline"]),
            "stage_artifact_digests": stage_outputs,
            "promoted_artifact_digest": checkpoint_candidates[-1] if checkpoint_candidates else None,
            "source_ids": list(source_refs),
            "evidence_run_ids": list(run_refs),
            "evaluation_digest": evaluation.get("evaluation_digest"),
            "quality_decision_digest": quality_decision.get("decision_digest"),
        }
        provenance["provenance_digest"] = canonical_digest(provenance)
        result = {
            "schema": "ananta.research-training-promotion-decision.v1",
            "run_id": run_id,
            "eligible": not reasons,
            "required_scope": required_scope,
            "reason_codes": sorted(set(reasons)),
            "automatic": True,
            "production_routes_changed": False,
            "human_intervention_required": False,
            "provenance_manifest": provenance,
        }
        result["decision_digest"] = canonical_digest(result)
        return result


__all__ = ["ResearchTrainingPromotionService"]
