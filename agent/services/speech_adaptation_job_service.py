"""Hub admission and delegation for immutable speech adaptation datasets.

This module remains the public entry point.  Admission value types and ports
live in ``speech_adaptation_admission_ports``, deterministic in-memory adapters
in ``speech_adaptation_in_memory_adapters`` and the pure worker-contract
assembly/validation in ``speech_adaptation_job_contract``; all previously
importable names are re-exported here for compatibility.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Mapping

from agent.models.speech_adaptation_admission import (
    SpeechAdaptationDecisionConflict,
    SpeechAdmissionDecision,
    SpeechCapacityLease,  # noqa: F401 - compatibility re-export
    SpeechPrincipal,
    restore_speech_adaptation_job,  # noqa: F401 - compatibility re-export
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_adaptation_admission_ports import (
    ActiveSpeechConsent,  # noqa: F401 - compatibility re-export
    AdmittedSpeechDataset,  # noqa: F401 - compatibility re-export
    SpeechAdaptationAdmissionError,
    SpeechAdaptationCurrentAuthorityPort,
    SpeechAdaptationDecisionStorePort,
    SpeechAdaptationLineagePort,
    SpeechAdaptationResultArtifactPort,
    SpeechCapacityLeasePort,
    SpeechConsentAdmissionPort,
    SpeechDatasetAdmissionPort,
)
from agent.services.speech_adaptation_in_memory_adapters import (
    InMemorySpeechAdaptationDecisionStore,
    InMemorySpeechCapacityLeasePort,  # noqa: F401 - compatibility re-export
)
from agent.services.speech_adaptation_job_contract import (
    build_speech_adaptation_job_payload,
    validate_prelease_bindings as _validate_prelease_bindings,
)
from agent.services.speech_adaptation_task_port import SpeechAdaptationTaskPort
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import (
    SpeechAdaptationContractError,
    SpeechAdaptationJob,
    SpeechAdaptationResult,
    canonical_sha256,
    speech_budget_digest,
    speech_configuration_digest,
    speech_scope_digest,
)


class SpeechAdaptationJobService:
    """Construct worker contracts only after current Hub admission succeeds."""

    def __init__(
        self,
        *,
        datasets: SpeechDatasetAdmissionPort,
        consents: SpeechConsentAdmissionPort,
        capacity: SpeechCapacityLeasePort,
        tasks: SpeechAdaptationTaskPort,
        model_catalog: Mapping[str, Mapping[str, str]],
        backend_catalog: Mapping[str, str],
        lineage: SpeechAdaptationLineagePort | None = None,
        decisions: SpeechAdaptationDecisionStorePort | None = None,
        current_authority: SpeechAdaptationCurrentAuthorityPort | None = None,
        result_artifacts: SpeechAdaptationResultArtifactPort | None = None,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._datasets = datasets
        self._consents = consents
        self._capacity = capacity
        self._tasks = tasks
        self._models = {str(key): dict(value) for key, value in model_catalog.items()}
        self._backends = {str(key): str(value) for key, value in backend_catalog.items()}
        if lineage is None:
            from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service

            lineage = get_ml_intern_speech_lineage_service()
        self._lineage = lineage
        self._decisions = decisions or InMemorySpeechAdaptationDecisionStore()
        self._current_authority = current_authority
        self._result_artifacts = result_artifacts
        self._now_ms = now_ms
        self._audit = audit

    def admit(
        self,
        principal: SpeechPrincipal,
        request: Mapping[str, Any],
        *,
        idempotency_key: str,
        _idempotency_digest_override: str | None = None,
        _replace_waiting: bool = False,
    ) -> SpeechAdmissionDecision:
        key = str(idempotency_key or "").strip()
        if _idempotency_digest_override is None and (
            not 8 <= len(key) <= 256 or any(character.isspace() for character in key)
        ):
            raise SpeechAdaptationAdmissionError("speech_idempotency_key_invalid", "bounded idempotency key required")
        allowed = {
            "dataset_id",
            "dataset_version",
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
            "budget",
            "deadline_at_ms",
            "capacity_policy",
        }
        if set(request) != allowed:
            raise SpeechAdaptationAdmissionError(
                "speech_admission_shape_invalid",
                "speech admission request has unknown or missing fields",
            )
        request_digest = canonical_sha256(dict(request))
        idempotency_digest = _idempotency_digest_override or canonical_sha256(
            {"key": key, "owner": principal.subject, "tenant": principal.tenant_id}
        )
        if len(idempotency_digest) != 64 or any(
            character not in "0123456789abcdef" for character in idempotency_digest
        ):
            raise SpeechAdaptationAdmissionError(
                "speech_idempotency_binding_invalid",
                "speech idempotency binding is invalid",
                status_code=409,
            )
        if not _replace_waiting:
            existing = self._decisions.by_idempotency(principal, idempotency_digest)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise SpeechAdaptationAdmissionError(
                        "speech_idempotency_conflict",
                        "idempotency binding changed",
                        status_code=409,
                    )
                self._record_audit(principal, existing)
                return existing
        now = int(self._now_ms())
        dataset_id = str(request.get("dataset_id") or "").strip()
        dataset_version = str(request.get("dataset_version") or "").strip()
        dataset = self._datasets.resolve(
            principal,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        if dataset is None:
            raise SpeechAdaptationAdmissionError(
                "speech_dataset_not_found",
                "speech dataset version was not found",
                status_code=404,
            )
        if (
            not dataset.immutable
            or dataset.status != "admitted"
            or dataset.tenant_id != principal.tenant_id
            or dataset.owner_subject != principal.subject
            or not dataset.storage_ref.startswith("artifact://speech-datasets/")
        ):
            raise SpeechAdaptationAdmissionError(
                "speech_dataset_not_admitted",
                "only an owned immutable admitted dataset can be trained",
            )
        pair_id = str(request.get("pair_id") or "").strip()
        direction = str(request.get("direction") or "").strip()
        speaker_digest = str(request.get("speaker_digest") or "").strip()
        scope_digest = speech_scope_digest(pair_id=pair_id, direction=direction, speaker_digest=speaker_digest)
        richer_consent_resolver = getattr(self._consents, "current_for_dataset", None)
        if callable(richer_consent_resolver):
            consent = richer_consent_resolver(
                principal,
                scope_digest=scope_digest,
                pair_id=pair_id,
                direction=direction,
                speaker_digest=speaker_digest,
                dataset=dataset,
            )
        else:
            consent = self._consents.current(principal, scope_digest=scope_digest)
        if (
            consent is None
            or not consent.granted
            or consent.purpose != "speech_adaptation_training"
            or consent.scope_digest != scope_digest
        ):
            raise SpeechAdaptationAdmissionError(
                "speech_consent_missing",
                "current scoped training consent is required",
            )
        model_id = str(request.get("base_model_id") or "").strip()
        model = self._models.get(model_id)
        if not model:
            raise SpeechAdaptationAdmissionError("speech_base_model_not_admitted", "base model is not admitted")
        backend = str(request.get("backend") or "").strip().casefold()
        backend_digest = self._backends.get(backend)
        if not backend_digest:
            raise SpeechAdaptationAdmissionError("speech_backend_not_admitted", "speech backend is not admitted")
        deadline = int(request.get("deadline_at_ms") or 0)
        if consent.expires_at_ms < deadline:
            raise SpeechAdaptationAdmissionError(
                "speech_consent_expires_before_deadline",
                "training consent expires before the requested deadline",
            )
        job_id = f"speech-job-{hashlib.sha256(f'{idempotency_digest}:{request_digest}'.encode()).hexdigest()[:32]}"
        waiting = self._decisions.get(principal, job_id) if _replace_waiting else None
        if _replace_waiting and (
            waiting is None
            or waiting.status != "queued"
            or waiting.job is not None
            or waiting.request_digest != request_digest
        ):
            raise SpeechAdaptationAdmissionError(
                "speech_capacity_wait_state_conflict",
                "speech capacity wait state is no longer promotable",
                status_code=409,
            )
        budget = dict(request.get("budget") or {})
        budget["budget_digest"] = speech_budget_digest(budget)
        configuration = {
            "backend": backend,
            "backend_digest": backend_digest,
            "seed": request.get("seed"),
            "max_steps": request.get("max_steps"),
            "batch_size": request.get("batch_size"),
            "checkpoint_interval_steps": request.get("checkpoint_interval_steps"),
            "learning_rate": request.get("learning_rate"),
            "scenario": request.get("scenario"),
        }
        configuration["config_digest"] = speech_configuration_digest(configuration)
        try:
            _validate_prelease_bindings(
                now_ms=now,
                deadline_at_ms=deadline,
                dataset=dataset,
                model_id=model_id,
                model=model,
                scope={
                    "pair_id": pair_id,
                    "direction": direction,
                    "speaker_digest": speaker_digest,
                    "scope_digest": scope_digest,
                },
                consent=consent,
                configuration=configuration,
                budget=budget,
            )
        except SpeechAdaptationContractError as exc:
            raise SpeechAdaptationAdmissionError(
                exc.reason_code,
                "speech admission request violates bounded training constraints",
                status_code=exc.status_code,
            ) from exc
        policy = str(request.get("capacity_policy") or "").strip()
        if policy not in {"queued", "dataset_only", "denied"}:
            raise SpeechAdaptationAdmissionError("speech_capacity_policy_invalid", "capacity policy is invalid")
        lease = self._capacity.try_acquire(job_id=job_id, deadline_at_ms=deadline, now_ms=now)
        if lease is None:
            policy_binding = canonical_sha256(
                {
                    "dataset_digest": dataset.dataset_digest,
                    "scope_digest": scope_digest,
                    "request_digest": request_digest,
                }
            )
            if waiting is None:
                ref = self._tasks.enqueue_policy_state(
                    job_id=job_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    status=policy,
                    reason_code="speech_capacity_unavailable",
                    binding_digest=policy_binding,
                )
                task_id = ref.task_id
            else:
                task_id = waiting.task_id
            decision = SpeechAdmissionDecision(
                job_id,
                task_id,
                policy,
                "speech_capacity_unavailable",
                None,
                request_digest,
                dict(request),
            )
            return self._remember(
                idempotency_digest,
                decision,
                principal,
                replace_waiting=_replace_waiting,
            )
        payload = build_speech_adaptation_job_payload(
            principal=principal,
            job_id=job_id,
            lease=lease,
            dataset=dataset,
            model_id=model_id,
            model=model,
            pair_id=pair_id,
            direction=direction,
            speaker_digest=speaker_digest,
            scope_digest=scope_digest,
            consent=consent,
            configuration=configuration,
            budget=budget,
            deadline=deadline,
        )
        try:
            job = SpeechAdaptationJob.from_mapping(payload, now_ms=now)
            self._lineage.publish_training_job(VoicePrincipal(principal.tenant_id, principal.subject), job)
            task = self._tasks.enqueue(job, tenant_id=principal.tenant_id, owner_subject=principal.subject)
        except SpeechAdaptationContractError as exc:
            self._capacity.release(lease.lease_id)
            raise SpeechAdaptationAdmissionError(
                exc.reason_code,
                "speech training contract could not be admitted",
                status_code=exc.status_code,
            ) from exc
        except Exception:
            self._capacity.release(lease.lease_id)
            raise
        decision = SpeechAdmissionDecision(
            job_id,
            waiting.task_id if waiting is not None else task.task_id,
            "queued",
            "speech_training_admitted",
            job,
            request_digest,
            dict(request),
        )
        return self._remember(
            idempotency_digest,
            decision,
            principal,
            replace_waiting=_replace_waiting,
        )

    def promote_waiting(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> SpeechAdmissionDecision:
        """Retry a durable capacity-wait decision without changing its binding."""

        waiting = self._decisions.waiting_admission(principal, job_id)
        if waiting is None:
            current = self.get(principal, job_id)
            if current.job is not None or current.status != "queued":
                return current
            raise SpeechAdaptationAdmissionError(
                "speech_capacity_wait_binding_missing",
                "speech capacity wait request cannot be reconstructed",
                status_code=409,
            )
        idempotency_digest, request = waiting
        return self.admit(
            principal,
            request,
            idempotency_key="internal-capacity-promotion",
            _idempotency_digest_override=idempotency_digest,
            _replace_waiting=True,
        )

    def accept_result(
        self,
        principal: SpeechPrincipal,
        job_id: str,
        result: SpeechAdaptationResult,
        *,
        authority: str = "hub",
    ) -> SpeechAdmissionDecision:
        """Accept a fenced worker result at the Hub boundary and publish lineage."""

        if authority != "hub":
            raise PermissionError("speech_training_hub_result_authority_required")
        decision = self.get(principal, job_id)
        if decision.job is None:
            raise SpeechAdaptationAdmissionError(
                "speech_training_result_not_expected",
                "policy-only speech job cannot accept a worker result",
                status_code=409,
            )
        if (
            result.job_id != decision.job.job_id
            or result.attempt_id != decision.job.attempt.attempt_id
            or result.binding_digest != decision.job.binding_digest
            or result.fencing_digest != decision.job.fencing.fencing_digest
        ):
            raise SpeechAdaptationAdmissionError(
                "speech_training_result_binding_mismatch",
                "speech training result does not match the active fenced attempt",
                status_code=409,
            )
        if self._current_authority is not None and result.status == "completed":
            active, reason = self._current_authority.verify_current(
                principal,
                decision.job,
                phase="before_result_accept",
            )
            if not active:
                raise SpeechAdaptationAdmissionError(
                    str(reason or "speech_training_authority_revoked"),
                    "speech training authority is no longer current",
                    status_code=409,
                )
        if self._result_artifacts is not None:
            try:
                self._result_artifacts.verify_and_commit(principal, decision.job, result)
            except SpeechAdaptationDecisionConflict as exc:
                raise SpeechAdaptationAdmissionError(
                    str(exc),
                    "speech training result did not match Hub-owned artifacts",
                    status_code=409,
                ) from exc
        self._lineage.publish_training_result(
            VoicePrincipal(principal.tenant_id, principal.subject),
            decision.job,
            result,
            authority=authority,
        )
        terminal = SpeechAdmissionDecision(
            decision.job_id,
            decision.task_id,
            result.status,
            result.reason_code or f"speech_training_{result.status}",
            decision.job,
            decision.request_digest,
            decision.admission_request,
            result,
        )
        try:
            terminal = self._decisions.replace(
                principal,
                terminal,
                expected_statuses=frozenset({"dispatching", "submitted", "running", "cancel_requested"}),
                result=result,
            )
        except SpeechAdaptationDecisionConflict as exc:
            raise SpeechAdaptationAdmissionError(
                str(exc),
                "speech training result lost its current Hub state",
                status_code=409,
            ) from exc
        self._capacity.release(decision.job.fencing.lease_id)
        finish = getattr(self._tasks, "finish", None)
        if callable(finish):
            finish(
                terminal.task_id,
                status=terminal.status,
                reason_code=terminal.reason_code,
            )
        self._record_audit(principal, terminal)
        return terminal

    def get(self, principal: SpeechPrincipal, job_id: str) -> SpeechAdmissionDecision:
        decision = self._decisions.get(principal, job_id)
        if decision is None:
            raise SpeechAdaptationAdmissionError(
                "speech_job_not_found",
                "speech adaptation job was not found",
                status_code=404,
            )
        return decision

    def evaluation_report(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> Mapping[str, Any]:
        decision = self.get(principal, job_id)
        if (
            decision.job is None
            or decision.result is None
            or decision.result.status != "completed"
            or decision.result.evaluation_report_digest is None
        ):
            raise SpeechAdaptationAdmissionError(
                "speech_evaluation_report_not_available",
                "speech evaluation report is not available",
                status_code=409,
            )
        if self._result_artifacts is None:
            raise SpeechAdaptationAdmissionError(
                "speech_evaluation_report_store_unavailable",
                "speech evaluation report store is not configured",
                status_code=503,
            )
        try:
            return self._result_artifacts.read_evaluation(
                principal,
                decision.job,
                decision.result.evaluation_report_digest,
            )
        except SpeechAdaptationDecisionConflict as exc:
            raise SpeechAdaptationAdmissionError(
                str(exc),
                "speech evaluation report could not be verified",
                status_code=409,
            ) from exc

    def cancel(
        self,
        principal: SpeechPrincipal,
        job_id: str,
        *,
        reason_code: str,
    ) -> SpeechAdmissionDecision:
        decision = self.get(principal, job_id)
        reason = str(reason_code or "").strip()
        if not reason or len(reason) > 128 or any(character.isspace() for character in reason):
            raise SpeechAdaptationAdmissionError(
                "speech_cancel_reason_invalid",
                "speech adaptation cancellation requires a bounded reason code",
            )
        if decision.status in {"completed", "dataset_only", "cancelled", "failed", "denied"}:
            self._record_audit(principal, decision)
            return decision
        worker_may_be_active = decision.status in {"dispatching", "submitted", "running", "cancel_requested"}
        next_status = "cancel_requested" if worker_may_be_active else "cancelled"
        cancelled = SpeechAdmissionDecision(
            decision.job_id,
            decision.task_id,
            next_status,
            reason,
            decision.job,
            decision.request_digest,
            decision.admission_request,
        )
        try:
            cancelled = self._decisions.replace(
                principal,
                cancelled,
                expected_statuses=frozenset({"queued", "dispatching", "submitted", "running", "cancel_requested"}),
            )
        except SpeechAdaptationDecisionConflict as exc:
            raise SpeechAdaptationAdmissionError(
                str(exc),
                "speech adaptation cancellation lost its current state",
                status_code=409,
            ) from exc
        if next_status == "cancelled":
            self._tasks.cancel(cancelled.task_id, reason_code=reason)
            if cancelled.job is not None:
                self._capacity.release(cancelled.job.fencing.lease_id)
        self._record_audit(principal, cancelled)
        return cancelled

    def _remember(
        self,
        idempotency_digest: str,
        decision: SpeechAdmissionDecision,
        principal: SpeechPrincipal,
        *,
        replace_waiting: bool = False,
    ) -> SpeechAdmissionDecision:
        try:
            if replace_waiting:
                saved = self._decisions.replace(
                    principal,
                    decision,
                    expected_statuses=frozenset({"queued"}),
                )
                replayed = False
            else:
                saved, replayed = self._decisions.create(
                    principal,
                    idempotency_digest=idempotency_digest,
                    decision=decision,
                )
        except SpeechAdaptationDecisionConflict as exc:
            raise SpeechAdaptationAdmissionError(
                str(exc),
                "speech adaptation decision could not be persisted",
                status_code=409,
            ) from exc
        if replayed and saved.request_digest != decision.request_digest:
            raise SpeechAdaptationAdmissionError(
                "speech_idempotency_conflict",
                "idempotency binding changed",
                status_code=409,
            )
        self._record_audit(principal, saved)
        return saved

    def record_worker_transition(
        self,
        principal: SpeechPrincipal,
        *,
        job_id: str,
        status: str,
        reason_code: str,
        job: SpeechAdaptationJob | None,
    ) -> None:
        """Audit one Hub-persisted dispatcher state without exposing storage.

        The dispatcher owns worker polling but not the audit schema. Keeping
        this narrow method on the domain service preserves that boundary and
        lets restart reconciliation replay the same idempotent command.
        """

        self._record_transition(
            principal,
            job_id=job_id,
            status=status,
            reason_code=reason_code,
            job=job,
        )

    def _record_audit(self, principal: SpeechPrincipal, decision: SpeechAdmissionDecision) -> None:
        self._record_transition(
            principal,
            job_id=decision.job_id,
            status=decision.status,
            reason_code=decision.reason_code,
            job=decision.job,
        )

    def _record_transition(
        self,
        principal: SpeechPrincipal,
        *,
        job_id: str,
        status: str,
        reason_code: str,
        job: SpeechAdaptationJob | None,
    ) -> None:
        if self._audit is None or bool(getattr(self._decisions, "transactional_audit", False)):
            return
        epoch = job.fencing.epoch if job is not None else 1
        lease_ref = job.fencing.lease_id if job is not None else None
        try:
            event = self._audit.prepare_transition(
                idempotency_key=f"speech-training:{job_id}:{status}:{reason_code}",
                tenant_id=principal.tenant_id,
                scope=f"speech-job:{job_id}",
                event_type="speech_training",
                transition=status,
                reason_code=reason_code,
                epoch=max(1, epoch),
                lease_ref=lease_ref,
                job_ref=job_id,
            )
            self._audit.append_prepared(event)
        except Exception as exc:
            raise SpeechAdaptationAdmissionError(
                "semantic_audit_unavailable",
                "speech training audit is unavailable",
                status_code=503,
            ) from exc
