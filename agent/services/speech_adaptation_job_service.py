"""Hub admission and delegation for immutable speech adaptation datasets."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_adaptation_task_port import SpeechAdaptationTaskPort
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    MAX_DEADLINE_AHEAD_MS,
    TRAIN_JOB_TYPE,
    SpeechAdaptationContractError,
    SpeechAdaptationJob,
    SpeechAdaptationResult,
    SpeechBaseModelBinding,
    SpeechConsentBinding,
    SpeechDatasetBinding,
    SpeechResourceBudget,
    SpeechScopeBinding,
    SpeechTrainingConfiguration,
    canonical_sha256,
    speech_attempt_digest,
    speech_budget_digest,
    speech_configuration_digest,
    speech_fencing_digest,
    speech_job_binding_digest,
    speech_scope_digest,
)


class SpeechAdaptationAdmissionError(ValueError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class SpeechPrincipal:
    tenant_id: str
    subject: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.subject.strip():
            raise ValueError("speech principal requires tenant and subject")


@dataclass(frozen=True)
class AdmittedSpeechDataset:
    dataset_id: str
    dataset_version: str
    tenant_id: str
    owner_subject: str
    storage_ref: str
    dataset_digest: str
    split_digest: str
    lineage_digest: str
    train_sample_count: int
    validation_sample_count: int
    immutable: bool
    status: str = "admitted"
    consent_bindings: tuple[tuple[str, int, int, str], ...] = ()
    contributor_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActiveSpeechConsent:
    consent_id: str
    version: int
    digest: str
    scope_digest: str
    purpose: str
    expires_at_ms: int
    export_allowed: bool
    granted: bool = True


@dataclass(frozen=True)
class SpeechCapacityLease:
    lease_id: str
    epoch: int
    expires_at_ms: int


class SpeechDatasetAdmissionPort(Protocol):
    def resolve(
        self,
        principal: SpeechPrincipal,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> AdmittedSpeechDataset | None: ...


class SpeechConsentAdmissionPort(Protocol):
    def current(self, principal: SpeechPrincipal, *, scope_digest: str) -> ActiveSpeechConsent | None: ...


class SpeechCapacityLeasePort(Protocol):
    def try_acquire(self, *, job_id: str, deadline_at_ms: int, now_ms: int) -> SpeechCapacityLease | None: ...

    def release(self, lease_id: str) -> None: ...


class SpeechAdaptationLineagePort(Protocol):
    def publish_training_job(self, principal: VoicePrincipal, job: SpeechAdaptationJob) -> str: ...

    def publish_training_result(
        self,
        principal: VoicePrincipal,
        job: SpeechAdaptationJob,
        result: SpeechAdaptationResult,
        *,
        authority: str = "hub",
    ) -> str: ...


class SpeechAdaptationDecisionConflict(RuntimeError):
    """Stable persistence conflict raised by a decision-store adapter."""


class SpeechAdaptationDecisionStorePort(Protocol):
    def by_idempotency(
        self,
        principal: SpeechPrincipal,
        idempotency_digest: str,
    ) -> "SpeechAdmissionDecision | None": ...

    def create(
        self,
        principal: SpeechPrincipal,
        *,
        idempotency_digest: str,
        decision: "SpeechAdmissionDecision",
    ) -> tuple["SpeechAdmissionDecision", bool]: ...

    def get(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> "SpeechAdmissionDecision | None": ...

    def waiting_admission(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> tuple[str, Mapping[str, Any]] | None: ...

    def replace(
        self,
        principal: SpeechPrincipal,
        decision: "SpeechAdmissionDecision",
        *,
        expected_statuses: frozenset[str],
        result: SpeechAdaptationResult | None = None,
    ) -> "SpeechAdmissionDecision": ...


class SpeechAdaptationCurrentAuthorityPort(Protocol):
    def verify_current(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        *,
        phase: str,
    ) -> tuple[bool, str | None]: ...


class SpeechAdaptationResultArtifactPort(Protocol):
    def verify_and_commit(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        result: SpeechAdaptationResult,
    ) -> None: ...

    def read_evaluation(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        evaluation_digest: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SpeechAdmissionDecision:
    job_id: str
    task_id: str
    status: str
    reason_code: str
    job: SpeechAdaptationJob | None
    request_digest: str
    admission_request: Mapping[str, Any] | None = None
    result: SpeechAdaptationResult | None = None


class InMemorySpeechAdaptationDecisionStore:
    """Compatibility/test adapter; production composition injects SQL."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[SpeechPrincipal, str], SpeechAdmissionDecision] = {}
        self._by_job: dict[tuple[SpeechPrincipal, str], SpeechAdmissionDecision] = {}
        self._lock = threading.RLock()

    def by_idempotency(
        self,
        principal: SpeechPrincipal,
        idempotency_digest: str,
    ) -> SpeechAdmissionDecision | None:
        with self._lock:
            return self._by_key.get((principal, idempotency_digest))

    def create(
        self,
        principal: SpeechPrincipal,
        *,
        idempotency_digest: str,
        decision: SpeechAdmissionDecision,
    ) -> tuple[SpeechAdmissionDecision, bool]:
        with self._lock:
            existing = self._by_key.get((principal, idempotency_digest))
            if existing is not None:
                if existing.request_digest != decision.request_digest:
                    raise SpeechAdaptationDecisionConflict("speech_idempotency_conflict")
                return existing, True
            self._by_key[(principal, idempotency_digest)] = decision
            self._by_job[(principal, decision.job_id)] = decision
            return decision, False

    def get(self, principal: SpeechPrincipal, job_id: str) -> SpeechAdmissionDecision | None:
        with self._lock:
            return self._by_job.get((principal, job_id))

    def waiting_admission(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> tuple[str, Mapping[str, Any]] | None:
        with self._lock:
            decision = self._by_job.get((principal, job_id))
            if (
                decision is None
                or decision.status != "queued"
                or decision.job is not None
                or decision.admission_request is None
            ):
                return None
            for (owner, digest), candidate in self._by_key.items():
                if owner == principal and candidate.job_id == job_id:
                    return digest, dict(decision.admission_request)
            return None

    def replace(
        self,
        principal: SpeechPrincipal,
        decision: SpeechAdmissionDecision,
        *,
        expected_statuses: frozenset[str],
        result: SpeechAdaptationResult | None = None,
    ) -> SpeechAdmissionDecision:
        del result
        with self._lock:
            current = self._by_job.get((principal, decision.job_id))
            if current is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            if current.status == decision.status and current.reason_code == decision.reason_code:
                return current
            if current.status not in expected_statuses:
                raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
            self._by_job[(principal, decision.job_id)] = decision
            for key, value in tuple(self._by_key.items()):
                if key[0] == principal and value.job_id == decision.job_id:
                    self._by_key[key] = decision
            return decision


class InMemorySpeechCapacityLeasePort:
    """Deterministic bounded lease port for native/single-Hub deployments and tests."""

    def __init__(self, capacity: int = 1, lease_seconds: int = 300) -> None:
        if not 1 <= capacity <= 128 or not 10 <= lease_seconds <= 3600:
            raise ValueError("speech capacity configuration is invalid")
        self._capacity = capacity
        self._lease_ms = lease_seconds * 1000
        self._lock = threading.RLock()
        self._leases: dict[str, SpeechCapacityLease] = {}
        self._epoch = 0

    def try_acquire(self, *, job_id: str, deadline_at_ms: int, now_ms: int) -> SpeechCapacityLease | None:
        with self._lock:
            self._leases = {key: value for key, value in self._leases.items() if value.expires_at_ms > now_ms}
            existing = self._leases.get(job_id)
            if existing is not None:
                return existing
            if len(self._leases) >= self._capacity:
                return None
            self._epoch += 1
            expires = min(deadline_at_ms, now_ms + self._lease_ms)
            lease = SpeechCapacityLease(
                lease_id=f"speech-lease-{hashlib.sha256(f'{job_id}:{self._epoch}'.encode()).hexdigest()[:32]}",
                epoch=self._epoch,
                expires_at_ms=expires,
            )
            self._leases[job_id] = lease
            return lease

    def release(self, lease_id: str) -> None:
        with self._lock:
            self._leases = {key: value for key, value in self._leases.items() if value.lease_id != lease_id}


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
        attempt_id = f"speech-attempt-{hashlib.sha256(f'{job_id}:{lease.epoch}'.encode()).hexdigest()[:32]}"
        attempt_digest = speech_attempt_digest(job_id=job_id, attempt_id=attempt_id, attempt_number=1)
        fencing_digest = speech_fencing_digest(
            attempt_id=attempt_id,
            epoch=lease.epoch,
            lease_id=lease.lease_id,
            lease_expires_at_ms=lease.expires_at_ms,
        )
        target_id = f"speech-adapter-{hashlib.sha256(job_id.encode()).hexdigest()[:32]}"
        tenant_ref = hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:32]
        target_ref = f"artifact://speech-adapters/{tenant_ref}/{target_id}"
        target_digest = canonical_sha256({"artifact_ref": target_ref, "target_id": target_id})
        binding_fields = {
            "artifact_target_digest": target_digest,
            "attempt_digest": attempt_digest,
            "budget_digest": budget["budget_digest"],
            "config_digest": configuration["config_digest"],
            "consent_digest": consent.digest,
            "dataset_digest": dataset.dataset_digest,
            "fencing_digest": fencing_digest,
            "lineage_digest": dataset.lineage_digest,
            "model_digest": model["model_digest"],
            "scope_digest": scope_digest,
            "split_digest": dataset.split_digest,
        }
        payload = {
            "contract_version": CONTRACT_VERSION,
            "job_type": TRAIN_JOB_TYPE,
            "job_id": job_id,
            "dataset": {
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.dataset_version,
                "storage_ref": dataset.storage_ref,
                "dataset_digest": dataset.dataset_digest,
                "split_digest": dataset.split_digest,
                "lineage_digest": dataset.lineage_digest,
                "train_sample_count": dataset.train_sample_count,
                "validation_sample_count": dataset.validation_sample_count,
                "immutable": True,
            },
            "base_model": {
                "model_id": model_id,
                "artifact_ref": model["artifact_ref"],
                "model_digest": model["model_digest"],
            },
            "scope": {
                "pair_id": pair_id,
                "direction": direction,
                "speaker_digest": speaker_digest,
                "scope_digest": scope_digest,
            },
            "consent": {
                "consent_id": consent.consent_id,
                "consent_version": consent.version,
                "consent_digest": consent.digest,
                "scope_digest": consent.scope_digest,
                "purpose": consent.purpose,
                "granted": consent.granted,
                "expires_at_ms": consent.expires_at_ms,
                "export_allowed": consent.export_allowed,
            },
            "configuration": configuration,
            "budget": budget,
            "attempt": {"attempt_id": attempt_id, "attempt_number": 1, "attempt_digest": attempt_digest},
            "fencing": {
                "lease_id": lease.lease_id,
                "epoch": lease.epoch,
                "lease_expires_at_ms": lease.expires_at_ms,
                "fencing_digest": fencing_digest,
            },
            "artifact_target": {
                "target_id": target_id,
                "artifact_ref": target_ref,
                "target_digest": target_digest,
            },
            "deadline_at_ms": deadline,
            "binding_digest": speech_job_binding_digest(binding_fields),
            "resume": None,
        }
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


def restore_speech_adaptation_job(payload: Mapping[str, Any]) -> SpeechAdaptationJob:
    """Validate a persisted contract without pretending its deadline is new."""

    deadline = int(payload.get("deadline_at_ms") or 0)
    budget = payload.get("budget") if isinstance(payload.get("budget"), Mapping) else {}
    wall_ms = int(budget.get("max_wall_seconds") or 0) * 1000
    fencing = payload.get("fencing") if isinstance(payload.get("fencing"), Mapping) else {}
    lease_expiry = int(fencing.get("lease_expires_at_ms") or 0)
    historical_now = max(0, min(deadline - wall_ms, lease_expiry - wall_ms))
    return SpeechAdaptationJob.from_mapping(payload, now_ms=historical_now)


def _validate_prelease_bindings(
    *,
    now_ms: int,
    deadline_at_ms: int,
    dataset: AdmittedSpeechDataset,
    model_id: str,
    model: Mapping[str, str],
    scope: Mapping[str, Any],
    consent: ActiveSpeechConsent,
    configuration: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> None:
    """Validate every lease-independent contract field before capacity policy."""

    SpeechDatasetBinding.from_mapping(
        {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "storage_ref": dataset.storage_ref,
            "dataset_digest": dataset.dataset_digest,
            "split_digest": dataset.split_digest,
            "lineage_digest": dataset.lineage_digest,
            "train_sample_count": dataset.train_sample_count,
            "validation_sample_count": dataset.validation_sample_count,
            "immutable": dataset.immutable,
        }
    )
    SpeechBaseModelBinding.from_mapping(
        {
            "model_id": model_id,
            "artifact_ref": model.get("artifact_ref"),
            "model_digest": model.get("model_digest"),
        }
    )
    parsed_scope = SpeechScopeBinding.from_mapping(scope)
    parsed_consent = SpeechConsentBinding.from_mapping(
        {
            "consent_id": consent.consent_id,
            "consent_version": consent.version,
            "consent_digest": consent.digest,
            "scope_digest": consent.scope_digest,
            "purpose": consent.purpose,
            "granted": consent.granted,
            "expires_at_ms": consent.expires_at_ms,
            "export_allowed": consent.export_allowed,
        }
    )
    parsed_configuration = SpeechTrainingConfiguration.from_mapping(configuration)
    parsed_budget = SpeechResourceBudget.from_mapping(budget)
    del parsed_configuration
    if parsed_consent.scope_digest != parsed_scope.scope_digest:
        raise SpeechAdaptationContractError(
            "speech_consent_scope_mismatch",
            "consent is not bound to the requested pair direction and speaker",
        )
    if isinstance(deadline_at_ms, bool) or deadline_at_ms <= now_ms:
        raise SpeechAdaptationContractError(
            "speech_deadline_stale",
            "speech training deadline has expired",
        )
    if deadline_at_ms - now_ms > MAX_DEADLINE_AHEAD_MS:
        raise SpeechAdaptationContractError(
            "speech_deadline_out_of_bounds",
            "speech training deadline exceeds the maximum admission horizon",
        )
    if parsed_consent.expires_at_ms < deadline_at_ms:
        raise SpeechAdaptationContractError(
            "speech_consent_expires_before_deadline",
            "consent must remain valid through the job deadline",
        )
    if parsed_budget.max_wall_seconds * 1000 > deadline_at_ms - now_ms:
        raise SpeechAdaptationContractError(
            "speech_budget_deadline_mismatch",
            "wall-time budget exceeds the admitted deadline",
        )
