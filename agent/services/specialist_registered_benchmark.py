"""Read technical benchmarks against current scoped registry authority."""

from dataclasses import replace
from typing import Protocol

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_adapter_registry_contract import AdapterRecord
from agent.services.specialist_promotion_gate import PromotionDecision
from ananta_contracts.specialist_decision import canonical_digest


class SpecialistRegistryReadPort(Protocol):
    def get(self, adapter_id: str, *, tenant_id: str, owner_subject: str) -> AdapterRecord | None: ...


class SpecialistBenchmarkReadPort(Protocol):
    def get_for_record(self, principal: MlInternTrainingPrincipal, record: AdapterRecord) -> dict: ...


class SpecialistRegisteredBenchmarkReader:
    """No approval or Evidence issuance; callers supply the Hub's gate decision."""

    def __init__(self, *, registry: SpecialistRegistryReadPort, reports: SpecialistBenchmarkReadPort,
                 allow_promotion_replay: bool = False):
        self._registry = registry
        self._reports = reports
        self._allow_promotion_replay = allow_promotion_replay

    def read(self, principal: MlInternTrainingPrincipal, adapter_id: str,
             decision: PromotionDecision, *, expected_record: AdapterRecord | None = None) -> dict:
        record = self._registry.get(
            adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject,
        )
        replay = self._is_promotion_replay(record, expected_record)
        if record is None or (record.status != "evaluated" and not replay):
            raise ValueError("specialist_registered_benchmark_unavailable")
        if expected_record is not None and record != expected_record and not replay:
            raise ValueError("specialist_registered_benchmark_registry_changed")
        payload = self._reports.get_for_record(principal, record)
        benchmark = payload.get("benchmark")
        lineage = payload.get("lineage")
        if not isinstance(benchmark, dict) or not isinstance(lineage, dict):
            raise ValueError("specialist_registered_benchmark_report_invalid")
        benchmark_body = {key: value for key, value in benchmark.items() if key != "result_digest"}
        if (canonical_digest(benchmark_body) != record.eval_report_ref
                or benchmark.get("holdout_digest") != lineage.get("holdout_digest")
                or benchmark.get("candidate_id") != payload.get("candidate_id")
                or lineage.get("training_job_id") != payload.get("training_job_id")
                or lineage.get("adapter_digest") != record.artifact_sha256
                or (benchmark.get("classification") or {}).get("macro_f1") != payload.get("aggregate_score")):
            raise ValueError("specialist_registered_benchmark_report_invalid")
        if (payload.get("quality_decision") != decision.to_mapping()
                or payload.get("passed") is not decision.allowed
                or record.eval_report_ref != decision.candidate_result_digest
                or payload.get("production_release_evidence") is not False):
            raise ValueError("specialist_registered_benchmark_decision_mismatch")
        current = self._registry.get(
            adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject,
        )
        if current != record:
            raise ValueError("specialist_registered_benchmark_registry_changed")
        return payload

    def _is_promotion_replay(self, record, expected):
        if (not self._allow_promotion_replay or record is None or expected is None
                or record.status != "approved" or expected.status != "evaluated"
                or len(record.promotion_history) != len(expected.promotion_history) + 1):
            return False
        promotion = record.promotion_history[-1]
        if (promotion.get("evaluation_id") != expected.eval_report_ref
                or promotion.get("artifact_sha256") != expected.artifact_sha256
                or promotion.get("revision_before") != expected.registry_version
                or promotion.get("revision_after") != expected.registry_version + 1
                or record.registry_version != expected.registry_version + 1):
            return False
        normalized = replace(
            record, status=expected.status, registry_version=expected.registry_version,
            approved_by=expected.approved_by, approved_at=expected.approved_at,
            approval_reason=expected.approval_reason, updated_at=expected.updated_at,
            promotion_history=expected.promotion_history,
        )
        # The registry still checks exact idempotency key, approver, reason and Evidence before replay.
        return normalized == expected


class SpecialistBenchmarkPromotionGuard:
    """Require the Hub's allowed native decision before a separate release gate."""

    def __init__(self, *, reader: SpecialistRegisteredBenchmarkReader, decision: PromotionDecision):
        self._reader = reader
        self._decision = decision

    def require(self, principal: MlInternTrainingPrincipal, record: AdapterRecord,
                *, expected_revision: int) -> None:
        if not self._decision.allowed:
            raise ValueError("specialist_promotion_quality_denied")
        if type(expected_revision) is not int or record.registry_version != expected_revision:
            raise ValueError("specialist_promotion_registry_revision_mismatch")
        payload = self._reader.read(principal, record.adapter_id, self._decision, expected_record=record)
        if payload.get("aggregate_score") != record.eval_score:
            raise ValueError("specialist_promotion_registry_score_mismatch")
