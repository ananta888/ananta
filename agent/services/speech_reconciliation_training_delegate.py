"""Separate Hub delegation boundary from reconciliation to speech training."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.services.ml_intern_speech_reconciled_dataset_service import ReconciledDatasetMaterialization
from agent.services.speech_adaptation_job_service import SpeechPrincipal
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import (
    SpeechAdaptationContractError,
    SpeechResourceBudget,
    speech_budget_digest,
)


class SpeechTrainingAdmissionPort(Protocol):
    def admit_dataset(
        self,
        principal: VoicePrincipal,
        *,
        dataset_id: str,
        dataset_version: str,
        manifest_digest: str,
        budget: Mapping[str, int],
        idempotency_key: str,
    ) -> str: ...


@dataclass(frozen=True)
class SpeechReconciliationTrainingDecision:
    status: str
    reason_code: str
    training_job_id: str | None


class SpeechReconciliationTrainingDelegate:
    def __init__(self, admission: SpeechTrainingAdmissionPort) -> None:
        self._admission = admission

    def delegate(
        self,
        principal: VoicePrincipal,
        materialization: ReconciledDatasetMaterialization,
        *,
        training_budget: Mapping[str, int] | None,
        idempotency_key: str,
        authority: str = "hub",
    ) -> SpeechReconciliationTrainingDecision:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_training_authority_required")
        if not materialization.trainable:
            return SpeechReconciliationTrainingDecision(
                "dataset_only_completed",
                "speech_reconciliation_dataset_not_trainable",
                None,
            )
        if (
            not training_budget
            or any(type(value) is not int or value < 0 for value in training_budget.values())
            or not any(value > 0 for value in training_budget.values())
        ):
            return SpeechReconciliationTrainingDecision(
                "dataset_only_completed",
                "speech_reconciliation_training_budget_missing",
                None,
            )
        manifest = dict(materialization.manifest)
        job_id = self._admission.admit_dataset(
            principal,
            dataset_id=str(manifest["dataset_id"]),
            dataset_version=str(manifest["version"]),
            manifest_digest=str(manifest["manifest_digest"]),
            budget=dict(training_budget),
            idempotency_key=idempotency_key,
        )
        return SpeechReconciliationTrainingDecision(
            "completed",
            "speech_reconciliation_training_delegated",
            job_id,
        )


class SpeechAdaptationJobAdmissionPort(Protocol):
    def admit(
        self,
        principal: SpeechPrincipal,
        request: Mapping[str, Any],
        *,
        idempotency_key: str,
    ): ...


class SpeechAdaptationTrainingAdmissionAdapter:
    """Adapt terminal reconciliation datasets to the existing job service.

    Scope, model and training configuration stay Hub-owned in the injected
    template; reconciliation output can only replace the immutable dataset
    binding and the explicitly supplied resource budget.
    """

    _TEMPLATE_FIELDS = frozenset(
        {
            "base_model_id",
            "pair_id",
            "direction",
            "speaker_digest",
            "backend",
            "seed",
            "max_steps",
            "batch_size",
            "checkpoint_interval_steps",
            "learning_rate",
            "scenario",
            "capacity_policy",
        }
    )

    def __init__(
        self,
        service: SpeechAdaptationJobAdmissionPort,
        *,
        request_template: Mapping[str, Any],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        template = dict(request_template)
        if set(template) != self._TEMPLATE_FIELDS:
            raise ValueError("speech_reconciliation_training_profile_invalid")
        self._service = service
        self._template = template
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def admit_dataset(
        self,
        principal: VoicePrincipal,
        *,
        dataset_id: str,
        dataset_version: str,
        manifest_digest: str,
        budget: Mapping[str, int],
        idempotency_key: str,
    ) -> str:
        if dataset_version != f"sha256:{manifest_digest}":
            raise ValueError("speech_reconciliation_training_dataset_binding_invalid")
        raw_budget = dict(budget)
        try:
            admitted_budget = SpeechResourceBudget.from_mapping(
                {**raw_budget, "budget_digest": speech_budget_digest(raw_budget)}
            )
        except SpeechAdaptationContractError as exc:
            raise ValueError("speech_reconciliation_training_budget_invalid") from exc
        wall_seconds = admitted_budget.max_wall_seconds
        request = {
            **self._template,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "budget": raw_budget,
            "deadline_at_ms": self._clock_ms() + wall_seconds * 1000,
        }
        decision = self._service.admit(
            SpeechPrincipal(principal.tenant_id, principal.subject),
            request,
            idempotency_key=idempotency_key,
        )
        if getattr(decision, "job", None) is None or getattr(decision, "status", None) != "queued":
            reason = str(getattr(decision, "reason_code", "speech_reconciliation_training_not_admitted"))
            raise ValueError(reason)
        return str(decision.job_id)


class RepositorySpeechReconciliationTrainingBudgetResolver:
    def __init__(self, repository) -> None:
        self._repository = repository

    def resolve(
        self,
        principal: VoicePrincipal,
        job,
    ) -> Mapping[str, int] | None:
        current = self._repository.get_job(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            job_id=job.job_id,
        )
        if current is None:
            return None
        return None if current.training_budget is None else dict(current.training_budget)


def build_speech_reconciliation_training_admission(
    service: SpeechAdaptationJobAdmissionPort,
    source: Mapping[str, str],
) -> SpeechAdaptationTrainingAdmissionAdapter:
    raw = str(source.get("ANANTA_SPEECH_RECONCILIATION_TRAINING_PROFILE_JSON") or "").strip()
    if not raw or len(raw.encode()) > 16 * 1024:
        raise ValueError("speech_reconciliation_training_profile_missing")
    try:
        profile = json.loads(raw, parse_constant=_reject_non_finite)
    except (TypeError, ValueError) as exc:
        raise ValueError("speech_reconciliation_training_profile_invalid") from exc
    if not isinstance(profile, dict) or any(not isinstance(key, str) for key in profile):
        raise ValueError("speech_reconciliation_training_profile_invalid")
    return SpeechAdaptationTrainingAdmissionAdapter(service, request_template=profile)


def _reject_non_finite(_value: str) -> None:
    raise ValueError("non-finite JSON is forbidden")


__all__ = [
    "SpeechReconciliationTrainingDecision",
    "SpeechReconciliationTrainingDelegate",
    "SpeechAdaptationTrainingAdmissionAdapter",
    "SpeechTrainingAdmissionPort",
    "RepositorySpeechReconciliationTrainingBudgetResolver",
    "build_speech_reconciliation_training_admission",
]
