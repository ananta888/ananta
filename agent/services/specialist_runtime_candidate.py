"""Preserve real Hub training artifacts when preparing a versioned specialist."""

from typing import Protocol

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_adapter_registry_service import AdapterRecord, MlInternAdapterRegistryService
from agent.services.specialist_evaluation_receipt_reader import SpecialistEvaluationReceiptReader
from agent.services.specialist_model_factory import CandidateOutcome
from agent.services.specialist_promotion_gate import PromotionDecision
from agent.services.specialist_training_outcome import SpecialistTrainingOutcomeService


class SpecialistBenchmarkReportPort(Protocol):
    def save(
        self, principal: MlInternTrainingPrincipal, winner: CandidateOutcome, decision: PromotionDecision,
    ) -> str: ...
    def bind(self, principal: MlInternTrainingPrincipal, record: AdapterRecord, reference: str) -> None: ...


class SpecialistRuntimeCandidateService:
    """Prepare an evaluated candidate; productive promotion needs separate Evidence admission."""

    def __init__(
        self, *, registry: MlInternAdapterRegistryService, outcomes: SpecialistTrainingOutcomeService,
        evaluation_receipts: SpecialistEvaluationReceiptReader | None = None,
    ):
        self._registry = registry
        self._outcomes = outcomes
        self._evaluation_receipts = evaluation_receipts

    def prepare(self, principal: MlInternTrainingPrincipal, winner: CandidateOutcome) -> AdapterRecord:
        if winner.evaluations:
            if self._evaluation_receipts is None or tuple(role for role, _ in winner.evaluations) != (
                "validation", "holdout"
            ):
                raise ValueError("specialist_runtime_candidate_evaluation_receipts_required")
            for role, receipt in winner.evaluations:
                if (
                    receipt.adapter_id != winner.outcome.adapter_id
                    or receipt.adapter_sha256 != winner.outcome.adapter_digest
                ):
                    raise ValueError("specialist_runtime_candidate_evaluation_receipt_mismatch")
                self._evaluation_receipts.read(
                    principal, receipt, partition=role, manifest_digest=winner.split_manifest_digest,
                    holdout_digest=winner.lineage.holdout_digest,
                )
        specialist = winner.job.request_spec.get("specialist") or {}
        lineage = winner.lineage
        digest = winner.result.to_mapping()["result_digest"]
        if (
            any(getattr(lineage, key) != specialist.get(key) for key in (
                "specialist_id", "contract_version", "dataset_digest", "holdout_digest"
            ))
            or not lineage.evaluated
            or lineage.benchmark_result_digest != digest
            or winner.result.holdout_digest != lineage.holdout_digest
            or winner.result.base_model != lineage.base_model
            or winner.result.method != lineage.method
            or winner.result.benchmark_version != specialist.get("benchmark_version")
        ):
            raise ValueError("specialist_runtime_candidate_evaluation_mismatch")
        checked = self._outcomes.read(principal, winner.outcome.job_id, winner.job)
        if checked != winner.outcome or (
            winner.lineage.training_job_id != checked.job_id
            or winner.lineage.adapter_digest != checked.adapter_digest
            or winner.lineage.base_model_digest != checked.base_model_digest
            or winner.lineage.base_model != winner.job.base_model
            or winner.lineage.method != winner.job.request_spec.get("method")
        ):
            raise ValueError("specialist_runtime_candidate_training_mismatch")
        original = self._registry.get(
            str(checked.adapter_id), tenant_id=principal.tenant_id, owner_subject=principal.subject
        )
        if original is None or original.artifact_sha256 != checked.adapter_digest:
            raise ValueError("specialist_runtime_candidate_registry_changed")
        record = self._registry.register_trained(
            adapter_id=f"specialist-{lineage.specialist_id}-{lineage.version}",
            display_name=f"{lineage.specialist_id} {lineage.version}", version=lineage.version,
            base_model=original.base_model, method=original.method,
            artifact_paths=dict(original.artifact_paths), config_hash=original.config_hash,
            artifact_sha256=original.artifact_sha256, dataset_hash=original.dataset_hash,
            source_ids=list(original.source_ids), run_ids=list(original.run_ids),
            provenance_verified=original.provenance_verified,
            task_kinds=[f"specialist:{lineage.specialist_id}"],
            notes=f"training_job={checked.job_id} lineage={lineage.lineage_digest}",
            release_target=original.release_target,
            tenant_id=principal.tenant_id, owner_subject=principal.subject,
        )
        if record.status not in {"trained", "evaluated"}:
            raise ValueError("specialist_runtime_candidate_lifecycle_conflict")
        score = float(winner.result.classification["macro_f1"])
        if record.status == "evaluated":
            if record.eval_report_ref != digest or record.eval_score != score:
                raise ValueError("specialist_runtime_candidate_evaluation_changed")
            return record
        return self._registry.set_eval_report(
            record.adapter_id, eval_report_ref=digest, eval_score=score,
            tenant_id=principal.tenant_id, owner_subject=principal.subject,
            expected_version=record.registry_version,
        )


class HubSpecialistCandidateRegistration:
    """Prepare scoped real candidates; approval remains a separate Hub Evidence gate."""

    def __init__(
        self, *, principal: MlInternTrainingPrincipal,
        candidates: SpecialistRuntimeCandidateService, registry: MlInternAdapterRegistryService,
        reports: SpecialistBenchmarkReportPort | None = None,
    ):
        self._principal = principal
        self._candidates = candidates
        self._registry = registry
        self._reports = reports

    def register(self, winner: CandidateOutcome, decision: PromotionDecision) -> tuple[str, str]:
        if decision.candidate_result_digest != winner.result.to_mapping()["result_digest"]:
            raise ValueError("specialist_runtime_candidate_decision_mismatch")
        record = self._candidates.prepare(self._principal, winner)
        if self._reports is not None:
            reference = self._reports.save(self._principal, winner, decision)
            self._reports.bind(self._principal, record, reference)
        if not decision.allowed:
            record = self._registry.reject(
                record.adapter_id, reason=",".join(decision.reason_codes),
                tenant_id=self._principal.tenant_id, owner_subject=self._principal.subject,
                expected_version=record.registry_version,
            )
        return record.adapter_id, record.status
