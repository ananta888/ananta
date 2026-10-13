"""Native specialist quality projected onto the existing Hub release gate."""

from typing import Protocol

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_adapter_registry_contract import AdapterRecord
from agent.services.ml_intern_evaluation_promotion_facade import EvaluationPromotionReadPort
from agent.services.ml_intern_provenance_contract import normalize_run_ids, normalize_source_ids
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt
from agent.services.specialist_prediction_artifact import SpecialistPredictionOutputs
from agent.services.specialist_promotion_gate import PromotionDecision
from agent.services.specialist_registered_benchmark import SpecialistRegisteredBenchmarkReader
from agent.services.specialist_release_execution import SpecialistExecutionBindingPort


class SpecialistPromotionReceiptPort(Protocol):
    def read(
        self, principal: MlInternTrainingPrincipal, receipt: SpecialistEvaluationReceipt,
        *, partition: str, manifest_digest: str, holdout_digest: str,
    ) -> SpecialistPredictionOutputs: ...


class SpecialistPromotionEvaluationReadModel:
    """Keep native metrics; obtain release provenance from admitted evaluation evidence."""

    def __init__(
        self, *, record: AdapterRecord, decision: PromotionDecision,
        benchmarks: SpecialistRegisteredBenchmarkReader, receipts: SpecialistPromotionReceiptPort,
        evaluation_evidence: EvaluationPromotionReadPort,
        execution_bindings: SpecialistExecutionBindingPort,
    ):
        self._record = record
        self._decision = decision
        self._benchmarks = benchmarks
        self._receipts = receipts
        self._evaluation_evidence = evaluation_evidence
        self._execution_bindings = execution_bindings

    def _report(self, principal, evaluation_id):
        if evaluation_id != self._record.eval_report_ref:
            raise ValueError("specialist_promotion_evaluation_reference_mismatch")
        return self._benchmarks.read(
            principal, self._record.adapter_id, self._decision, expected_record=self._record,
        )

    def get(self, principal: MlInternTrainingPrincipal, evaluation_id: str) -> dict:
        report = self._report(principal, evaluation_id)
        return {"id": evaluation_id, "passed": report["passed"],
                "aggregate_score": report["aggregate_score"], "score_kind": report["score_kind"]}

    def get_promotion_evidence(self, principal: MlInternTrainingPrincipal, evaluation_id: str) -> dict:
        report = self._report(principal, evaluation_id)
        evaluations = report.get("evaluations")
        if not isinstance(evaluations, dict) or set(evaluations) != {"validation", "holdout"}:
            raise ValueError("specialist_promotion_evaluation_receipts_required")
        checked = {}
        for role in ("validation", "holdout"):
            try:
                receipt = SpecialistEvaluationReceipt(**evaluations[role])
            except (TypeError, ValueError) as exc:
                raise ValueError("specialist_promotion_evaluation_receipt_invalid") from exc
            if (receipt.adapter_id != report["adapter_id"]
                    or receipt.adapter_sha256 != report["artifact_sha256"]
                    or receipt.base_model_sha256 != report["lineage"]["base_model_digest"]):
                raise ValueError("specialist_promotion_evaluation_receipt_binding_mismatch")
            self._receipts.read(
                principal, receipt, partition=role, manifest_digest=report["split_manifest_digest"],
                holdout_digest=report["lineage"]["holdout_digest"],
            )
            checked[role] = receipt
        if checked["validation"].job_id == checked["holdout"].job_id:
            raise ValueError("specialist_promotion_evaluation_roles_reused")
        admitted = {
            role: self._read_execution_evidence(principal, report, receipt)
            for role, receipt in checked.items()
        }
        evidence = dict(admitted["holdout"])
        for field, normalizer in (("source_ids", normalize_source_ids), ("run_ids", normalize_run_ids)):
            references = [normalizer(item.get(field)) for item in admitted.values()]
            if any(not values for values in references):
                raise ValueError("specialist_promotion_execution_evidence_references_required")
            evidence[field] = list(normalizer(sorted({value for values in references for value in values})))
        self._execution_bindings.require(principal, report, evidence)
        # Recheck current report/registry after consuming independently admitted evidence.
        if self._report(principal, evaluation_id) != report:
            raise ValueError("specialist_promotion_evaluation_changed")
        return evidence

    def _read_execution_evidence(self, principal, report, receipt):
        evidence = self._evaluation_evidence.get_promotion_evidence(principal, receipt.job_id)
        expected = {
            "job_id": receipt.job_id, "attempt_id": receipt.attempt_id,
            "adapter_id": receipt.adapter_id, "artifact_sha256": receipt.adapter_sha256,
            "base_model_id": report["lineage"]["base_model"],
            "base_model_sha256": receipt.base_model_sha256,
        }
        if not isinstance(evidence, dict) or any(evidence.get(key) != value for key, value in expected.items()):
            raise ValueError("specialist_promotion_execution_evidence_mismatch")
        return evidence
