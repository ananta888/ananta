"""Evaluation policy and immutable promotion gate for training artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_storage_contracts import StorageReferencePort
from agent.services.unsloth_task_port import UnslothAuditPort


class PromotionGateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EvaluationSnapshot:
    evaluation_id: str
    tenant_id: str
    artifact_id: str
    artifact_sha256: str
    dataset_hash: str
    state: str
    metrics: Mapping[str, float]
    source_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    job_id: str | None = None
    attempt_id: str | None = None
    fencing_token_digest: str | None = None
    base_model_id: str | None = None
    base_model_sha256: str | None = None
    adapter_id: str | None = None
    adapter_sha256: str | None = None
    export_sha256: str | None = None


class EvaluationCatalogPort(Protocol):
    def get(
        self,
        *,
        tenant_id: str,
        evaluation_id: str,
    ) -> EvaluationSnapshot | None: ...


class ArtifactPromotionPort(Protocol):
    def promote(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        artifact_sha256: str,
        evaluation_id: str,
        expected_revision: int,
        evidence: Mapping[str, object],
    ) -> int: ...


@dataclass(frozen=True)
class PromotionRequest:
    tenant_id: str
    artifact_id: str
    artifact_sha256: str
    dataset_hash: str
    evaluation_id: str
    minimum_metrics: Mapping[str, float]
    expected_registry_revision: int
    job_id: str | None = None
    attempt_id: str | None = None
    fencing_token_digest: str | None = None
    base_model_id: str | None = None
    base_model_sha256: str | None = None
    adapter_id: str | None = None
    adapter_sha256: str | None = None
    export_sha256: str | None = None


@dataclass(frozen=True)
class PromotionPlan:
    tenant_id: str
    payload_json: str
    confirmation_digest: str


class UnslothEvaluationPromotionService:
    """Separates policy evaluation from the registry mutation."""

    _SHA256 = re.compile(r"^[0-9a-f]{64}$")

    def __init__(
        self,
        *,
        evaluations: EvaluationCatalogPort,
        promotions: ArtifactPromotionPort,
        evidence: ProvidedEvidenceRegistry,
        audit: UnslothAuditPort,
        storage_references: StorageReferencePort | None = None,
    ) -> None:
        self._evaluations = evaluations
        self._promotions = promotions
        self._evidence = evidence
        self._audit = audit
        self._storage_references = storage_references

    def plan(self, request: PromotionRequest) -> PromotionPlan:
        snapshot = self._evaluations.get(
            tenant_id=request.tenant_id,
            evaluation_id=request.evaluation_id,
        )
        if snapshot is None:
            raise PromotionGateError(
                "evaluation_not_found",
                "The evaluation is unavailable in the tenant catalog.",
            )
        references = self._validate(request, snapshot)
        payload = {
            "schema_version": 1,
            "tenant_id": request.tenant_id,
            "artifact_id": request.artifact_id,
            "artifact_sha256": request.artifact_sha256,
            "dataset_hash": request.dataset_hash,
            "evaluation_id": request.evaluation_id,
            "minimum_metrics": dict(sorted(request.minimum_metrics.items())),
            "observed_metrics": dict(sorted(snapshot.metrics.items())),
            "source_ids": list(references.source_ids),
            "run_ids": list(references.run_ids),
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "fencing_token_digest": request.fencing_token_digest,
            "base_model_id": request.base_model_id,
            "base_model_sha256": request.base_model_sha256,
            "adapter_id": request.adapter_id,
            "adapter_sha256": request.adapter_sha256,
            "export_sha256": request.export_sha256,
            "expected_registry_revision": request.expected_registry_revision,
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        confirmation = hashlib.sha256(
            f"unsloth-artifact-promotion:{payload_json}".encode()
        ).hexdigest()
        return PromotionPlan(
            tenant_id=request.tenant_id,
            payload_json=payload_json,
            confirmation_digest=confirmation,
        )

    def promote(
        self,
        plan: PromotionPlan,
        *,
        confirmation_digest: str,
    ) -> int:
        expected = hashlib.sha256(
            f"unsloth-artifact-promotion:{plan.payload_json}".encode()
        ).hexdigest()
        if (
            not hmac.compare_digest(confirmation_digest, plan.confirmation_digest)
            or not hmac.compare_digest(confirmation_digest, expected)
        ):
            raise PromotionGateError(
                "promotion_confirmation_invalid",
                "The evaluated promotion plan must be explicitly confirmed.",
            )
        payload = json.loads(plan.payload_json)
        if self._storage_references is not None:
            try:
                self._storage_references.bind_reference(
                    tenant_id=plan.tenant_id,
                    reference_kind="promotion",
                    reference_id=(
                        f"{payload['artifact_id']}:"
                        f"{payload['expected_registry_revision'] + 1}"
                    ),
                    artifact_id=payload["artifact_id"],
                    artifact_sha256=payload["artifact_sha256"],
                )
            except Exception as exc:
                raise PromotionGateError(
                    "promotion_storage_binding_failed",
                    "Promotion storage reference could not be bound.",
                ) from exc
        revision = self._promotions.promote(
            tenant_id=plan.tenant_id,
            artifact_id=payload["artifact_id"],
            artifact_sha256=payload["artifact_sha256"],
            evaluation_id=payload["evaluation_id"],
            expected_revision=payload["expected_registry_revision"],
            evidence={
                "dataset_hash": payload["dataset_hash"],
                "source_ids": payload["source_ids"],
                "run_ids": payload["run_ids"],
                "metrics": payload["observed_metrics"],
                "job_id": payload["job_id"],
                "attempt_id": payload["attempt_id"],
                "fencing_token_digest": payload["fencing_token_digest"],
                "base_model_id": payload["base_model_id"],
                "base_model_sha256": payload["base_model_sha256"],
                "adapter_id": payload["adapter_id"],
                "adapter_sha256": payload["adapter_sha256"],
                "export_sha256": payload["export_sha256"],
            },
        )
        self._audit.record(
            event_type="unsloth.artifact_promoted",
            tenant_id=plan.tenant_id,
            subject_id=payload["artifact_id"],
            details={
                "artifact_sha256": payload["artifact_sha256"],
                "evaluation_id": payload["evaluation_id"],
                "registry_revision": revision,
            },
        )
        return revision

    def _validate(
        self,
        request: PromotionRequest,
        snapshot: EvaluationSnapshot,
    ):
        if not request.tenant_id or not request.artifact_id:
            raise PromotionGateError(
                "promotion_scope_missing",
                "Tenant and artifact IDs are required.",
            )
        if snapshot.tenant_id != request.tenant_id:
            raise PromotionGateError(
                "promotion_tenant_mismatch",
                "The evaluation belongs to another tenant.",
            )
        if snapshot.state != "passed":
            raise PromotionGateError(
                "evaluation_not_passed",
                "Only passed evaluations can authorize promotion.",
            )
        if (
            snapshot.artifact_id != request.artifact_id
            or not hmac.compare_digest(
                snapshot.artifact_sha256,
                request.artifact_sha256,
            )
            or not hmac.compare_digest(
                snapshot.dataset_hash,
                request.dataset_hash,
            )
        ):
            raise PromotionGateError(
                "promotion_provenance_mismatch",
                "Evaluation provenance does not match the requested artifact.",
            )
        identity_pairs = (
            ("job_id", request.job_id, snapshot.job_id),
            ("attempt_id", request.attempt_id, snapshot.attempt_id),
            ("base_model_id", request.base_model_id, snapshot.base_model_id),
            ("adapter_id", request.adapter_id, snapshot.adapter_id),
        )
        if any(
            not requested
            or not observed
            or not hmac.compare_digest(str(requested), str(observed))
            for _name, requested, observed in identity_pairs
        ):
            raise PromotionGateError(
                "promotion_execution_identity_mismatch",
                "Evaluation job, attempt, model, or adapter identity is not bound.",
            )
        hash_pairs = (
            (
                "fencing_token_digest",
                request.fencing_token_digest,
                snapshot.fencing_token_digest,
            ),
            ("base_model_sha256", request.base_model_sha256, snapshot.base_model_sha256),
            ("adapter_sha256", request.adapter_sha256, snapshot.adapter_sha256),
            ("export_sha256", request.export_sha256, snapshot.export_sha256),
        )
        if any(
            not requested
            or not observed
            or not hmac.compare_digest(str(requested), str(observed))
            for _name, requested, observed in hash_pairs
        ):
            raise PromotionGateError(
                "promotion_execution_hash_mismatch",
                "Evaluation fence, model, adapter, or export hash is not bound.",
            )
        for value in (
            request.artifact_sha256,
            request.dataset_hash,
            request.fencing_token_digest,
            request.base_model_sha256,
            request.adapter_sha256,
            request.export_sha256,
        ):
            if not self._SHA256.fullmatch(value):
                raise PromotionGateError(
                    "promotion_hash_invalid",
                    "Promotion evidence hashes must be lowercase SHA-256 values.",
                )
        if request.expected_registry_revision < 0:
            raise PromotionGateError(
                "promotion_revision_invalid",
                "A non-negative registry revision fence is required.",
            )
        if not request.minimum_metrics:
            raise PromotionGateError(
                "promotion_policy_missing",
                "At least one minimum evaluation metric is required.",
            )
        for metric, minimum in request.minimum_metrics.items():
            observed = snapshot.metrics.get(metric)
            if observed is None or observed < minimum:
                raise PromotionGateError(
                    "promotion_metric_failed",
                    f"Evaluation metric {metric!r} does not meet policy.",
                )
        return self._evidence.resolve(
            source_ids=snapshot.source_ids,
            run_ids=snapshot.run_ids,
        )
