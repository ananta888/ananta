"""Canonical Hub composition for Unsloth evaluation promotion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent.services.ml_intern_adapter_registry_service import (
    AdapterRecord,
    MlInternAdapterRegistryService,
)
from agent.services.ml_intern_evaluation_store_service import (
    MlInternEvaluationStoreService,
)
from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_evaluation_promotion_service import (
    EvaluationSnapshot,
    PromotionGateError,
    PromotionRequest,
    UnslothEvaluationPromotionService,
)
from agent.services.unsloth_evidence import (
    EvidenceVerificationError,
    ProvidedEvidenceRegistry,
)
from agent.services.unsloth_storage_contracts import StorageReferencePort


class MlInternEvaluationPromotionFacade:
    """Binds verified evaluation evidence to one atomic registry promotion."""

    def __init__(
        self,
        *,
        evaluations: MlInternEvaluationStoreService,
        registry: MlInternAdapterRegistryService,
        trusted_source_ids: tuple[str, ...],
        trusted_run_ids: tuple[str, ...],
        audit_sink: Callable[[str, Mapping[str, Any]], None],
        storage_references: StorageReferencePort | None = None,
    ) -> None:
        self._evaluations = evaluations
        self._registry = registry
        self._evidence = ProvidedEvidenceRegistry(
            source_ids=trusted_source_ids,
            run_ids=trusted_run_ids,
        )
        self._audit_sink = audit_sink
        self._storage_references = storage_references

    def promote(
        self,
        principal: MlInternTrainingPrincipal,
        record: AdapterRecord,
        *,
        expected_revision: int,
        idempotency_key: str,
        approved_by: str,
        reason: str,
        minimum_score: float,
    ) -> tuple[AdapterRecord, bool]:
        evaluation_id = str(record.eval_report_ref or "")
        evaluation = self._evaluations.get(principal, evaluation_id)
        evidence = self._evaluations.get_promotion_evidence(
            principal,
            evaluation_id,
        )
        snapshot = EvaluationSnapshot(
            evaluation_id=evaluation_id,
            tenant_id=principal.tenant_id,
            artifact_id=record.adapter_id,
            artifact_sha256=str(evidence.get("artifact_sha256") or ""),
            dataset_hash=str(evidence.get("dataset_hash") or ""),
            state="passed" if evaluation.get("passed") is True else "failed",
            metrics={
                "aggregate_score": float(
                    evaluation.get("aggregate_score") or 0.0
                )
            },
            source_ids=tuple(evidence.get("source_ids") or ()),
            run_ids=tuple(evidence.get("run_ids") or ()),
            job_id=str(evidence.get("job_id") or ""),
            attempt_id=str(evidence.get("attempt_id") or ""),
            fencing_token_digest=str(
                evidence.get("fencing_token_digest") or ""
            ),
            base_model_id=str(evidence.get("base_model_id") or ""),
            base_model_sha256=str(evidence.get("base_model_sha256") or ""),
            adapter_id=str(evidence.get("adapter_id") or ""),
            adapter_sha256=str(evidence.get("adapter_sha256") or ""),
            export_sha256=str(evidence.get("export_sha256") or ""),
        )
        catalog = _SingleEvaluationCatalog(snapshot)
        promotions = _RegistryPromotionPort(
            self._registry,
            principal=principal,
            approved_by=approved_by,
            reason=reason,
            idempotency_key=idempotency_key,
            minimum_score=minimum_score,
        )
        service = UnslothEvaluationPromotionService(
            evaluations=catalog,
            promotions=promotions,
            evidence=self._evidence,
            audit=_AuditAdapter(self._audit_sink),
            storage_references=self._storage_references,
        )
        request = PromotionRequest(
            tenant_id=principal.tenant_id,
            artifact_id=record.adapter_id,
            artifact_sha256=str(record.artifact_sha256 or ""),
            dataset_hash=str(evidence.get("dataset_hash") or ""),
            evaluation_id=evaluation_id,
            minimum_metrics={"aggregate_score": minimum_score},
            expected_registry_revision=expected_revision,
            job_id=snapshot.job_id,
            attempt_id=snapshot.attempt_id,
            fencing_token_digest=snapshot.fencing_token_digest,
            base_model_id=snapshot.base_model_id,
            base_model_sha256=snapshot.base_model_sha256,
            adapter_id=snapshot.adapter_id,
            adapter_sha256=snapshot.adapter_sha256,
            export_sha256=snapshot.export_sha256,
        )
        try:
            plan = service.plan(request)
            service.promote(
                plan,
                confirmation_digest=plan.confirmation_digest,
            )
        except EvidenceVerificationError as exc:
            raise PromotionGateError(exc.code, str(exc)) from exc
        if promotions.record is None:
            raise PromotionGateError(
                "promotion_registry_result_missing",
                "Registry promotion did not return an adapter record.",
            )
        return promotions.record, promotions.replayed


class _SingleEvaluationCatalog:
    def __init__(self, snapshot: EvaluationSnapshot) -> None:
        self._snapshot = snapshot

    def get(
        self,
        *,
        tenant_id: str,
        evaluation_id: str,
    ) -> EvaluationSnapshot | None:
        if (
            tenant_id != self._snapshot.tenant_id
            or evaluation_id != self._snapshot.evaluation_id
        ):
            return None
        return self._snapshot


class _RegistryPromotionPort:
    def __init__(
        self,
        registry: MlInternAdapterRegistryService,
        *,
        principal: MlInternTrainingPrincipal,
        approved_by: str,
        reason: str,
        idempotency_key: str,
        minimum_score: float,
    ) -> None:
        self._registry = registry
        self._principal = principal
        self._approved_by = approved_by
        self._reason = reason
        self._idempotency_key = idempotency_key
        self._minimum_score = minimum_score
        self.record: AdapterRecord | None = None
        self.replayed = False

    def promote(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        artifact_sha256: str,
        evaluation_id: str,
        expected_revision: int,
        evidence: Mapping[str, object],
    ) -> int:
        if tenant_id != self._principal.tenant_id:
            raise PromotionGateError(
                "promotion_tenant_mismatch",
                "Promotion port tenant does not match its principal.",
            )
        self.record, self.replayed = self._registry.promote_evaluated(
            artifact_id,
            artifact_sha256=artifact_sha256,
            evaluation_id=evaluation_id,
            evidence=dict(evidence),
            approved_by=self._approved_by,
            reason=self._reason,
            idempotency_key=self._idempotency_key,
            tenant_id=self._principal.tenant_id,
            owner_subject=self._principal.subject,
            expected_version=expected_revision,
            minimum_eval_score=self._minimum_score,
        )
        return self.record.registry_version


class _AuditAdapter:
    def __init__(
        self,
        sink: Callable[[str, Mapping[str, Any]], None],
    ) -> None:
        self._sink = sink

    def record(
        self,
        *,
        event_type: str,
        tenant_id: str,
        subject_id: str,
        details: Mapping[str, object],
    ) -> None:
        self._sink(
            event_type,
            {
                "tenant_id": tenant_id,
                "subject_id": subject_id,
                **dict(details),
            },
        )


__all__ = ["MlInternEvaluationPromotionFacade", "PromotionGateError"]
