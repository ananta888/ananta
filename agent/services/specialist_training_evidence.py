"""Pre-execution Hub reservation and fenced completion for specialist jobs."""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent.db_models import MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.ports.evidence_identity import SourceEvidenceIdentity
from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryError, HubEvidenceRegistryService
from agent.services.specialist_dataset_binding import require_specialist_dataset_binding, specialist_dataset_binding
from agent.services.specialist_job_predictions import SpecialistJobReadPort
from ananta_contracts.lora_evidence import lora_evidence_assignment
from ananta_contracts.specialist_decision import canonical_digest

ASSIGNMENT_KEY = "_hub_evidence_assignment"


class SpecialistTrainingEvidenceError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SpecialistRunPolicyContext:
    """Hub-owned policy and already admitted immutable inputs; never Worker fields."""

    project_id: str
    repository_revision: str
    execution_profile_digest: str
    environment_digest: str
    base_model_digest: str
    evidence_scope: str
    synthetic: bool
    sources: tuple[SourceEvidenceIdentity, ...]


class SpecialistRunContextPort(Protocol):
    def read(self, principal: MlInternTrainingPrincipal, job: MlInternTrainingJobDB) -> SpecialistRunPolicyContext: ...


class SpecialistTrainingEvidencePort(Protocol):
    def reserve(
        self, principal: MlInternTrainingPrincipal, job: MlInternTrainingJobDB,
        attempt: MlInternTrainingAttemptDB,
    ) -> dict | None: ...
    def complete(self, principal: MlInternTrainingPrincipal, job: MlInternTrainingJobDB) -> None: ...


class SpecialistTrainingEvidenceService:
    """Consume Hub policy/input admission; only the Hub Registry issues identities."""

    def __init__(self, *, repository: SpecialistJobReadPort, registry: HubEvidenceRegistryService,
                 contexts: SpecialistRunContextPort, model_catalog: Mapping[str, Mapping[str, Any]],
                 clock: Callable[[], float] = time.time):
        self._repository = repository
        self._registry = registry
        self._contexts = contexts
        self._models = {key: dict(value) for key, value in model_catalog.items()}
        self._clock = clock

    def _current(self, principal, job):
        current = self._repository.get_job(principal, job.id)
        if (current is None or current != job or job.tenant_id != principal.tenant_id
                or job.owner_subject != principal.subject or not job.worker_job_id):
            raise SpecialistTrainingEvidenceError("specialist_evidence_job_changed")
        attempt = self._repository.get_attempt(str(job.active_attempt_id or ""))
        if (attempt is None or attempt.job_id != job.id or attempt.tenant_id != principal.tenant_id
                or attempt.owner_subject != principal.subject):
            raise SpecialistTrainingEvidenceError("specialist_evidence_attempt_invalid")
        return attempt

    def _context(self, principal, job):
        context = self._contexts.read(principal, job)
        dataset = self._repository.get_dataset(principal, str(job.dataset_id or ""))
        if dataset is None:
            raise SpecialistTrainingEvidenceError("specialist_evidence_dataset_missing")
        require_specialist_dataset_binding(job.request_spec, dataset)
        model_digest = (self._models.get(str(job.base_model)) or {}).get("snapshot_hash")
        if not model_digest or context.base_model_digest != model_digest:
            raise SpecialistTrainingEvidenceError("specialist_evidence_base_model_mismatch")
        dataset_origin = canonical_digest({"dataset_binding": specialist_dataset_binding(dataset)})
        model_origin = canonical_digest({"model_id": job.base_model, "snapshot_hash": model_digest})
        sources = []
        for pin in context.sources:
            source = self._registry.require_source_identity(
                tenant_id=principal.tenant_id, project_id=context.project_id, source_id=pin.source_id,
                expected_binding_digest=pin.binding_digest,
            )
            if source != pin:
                raise SpecialistTrainingEvidenceError("specialist_evidence_source_changed")
            sources.append(source)
        if (not any(source.origin_type == "specialist_dataset" and source.content_digest == dataset.content_sha256
                    and source.origin_digest == dataset_origin
                    for source in sources)
                or not any(source.origin_type == "specialist_base_model"
                           and source.content_digest == context.base_model_digest
                           and source.origin_digest == model_origin for source in sources)):
            raise SpecialistTrainingEvidenceError("specialist_evidence_source_inputs_missing")
        return context

    def reserve(self, principal, job, attempt):
        if job.request_spec.get("task_family") != "specialist_decision":
            return None
        current_attempt = self._current(principal, job)
        if (current_attempt != attempt or job.status not in {"claimed", "running"}
                or attempt.status not in {"claimed", "running"}
                or self._clock() >= min(attempt.deadline_at, attempt.lease_expires_at)
                or ASSIGNMENT_KEY in job.result_summary):
            raise SpecialistTrainingEvidenceError("specialist_evidence_pre_execution_attempt_required")
        context = self._context(principal, job)
        run = self._registry.reserve_run(
            tenant_id=principal.tenant_id, project_id=context.project_id, task_id=job.task_id,
            assignment_id=job.worker_job_id, dispatch_lease_id=attempt.id,
            repository_revision=context.repository_revision, input_digest=job.request_digest,
            execution_profile_digest=context.execution_profile_digest, environment_digest=context.environment_digest,
            source_ids=[source.source_id for source in context.sources], evidence_scope=context.evidence_scope,
            synthetic=context.synthetic, idempotency_key=f"specialist-training:{job.id}:{attempt.id}",
        )
        # Revalidate before the projection is persisted or sent to a Worker.
        if self._current(principal, job) != attempt or self._context(principal, job) != context:
            raise SpecialistTrainingEvidenceError("specialist_evidence_reservation_changed")
        return self._registry.assignment_projection(
            tenant_id=principal.tenant_id, project_id=context.project_id, run_id=run.run_id,
            task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=attempt.id,
            input_digest=job.request_digest, expected_binding_digest=run.binding_digest,
        )

    def complete(self, principal, job):
        if job.request_spec.get("task_family") != "specialist_decision":
            return
        if ASSIGNMENT_KEY not in job.result_summary:
            return  # Reservation failed before execution; no post-hoc identity issuance.
        attempt = self._current(principal, job)
        states = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled"}
        if job.status not in states or attempt.status != job.status:
            raise SpecialistTrainingEvidenceError("specialist_evidence_terminal_attempt_required")
        context = self._context(principal, job)
        projection = lora_evidence_assignment(job.result_summary[ASSIGNMENT_KEY], attempt_id=attempt.id)
        if (projection["task_id"] != job.task_id or projection["assignment_id"] != job.worker_job_id
                or set(projection["source_ids"]) != {source.source_id for source in context.sources}):
            raise SpecialistTrainingEvidenceError("specialist_evidence_completion_binding_mismatch")
        result_digest = canonical_digest(job.result_summary)
        lookup = dict(
            tenant_id=principal.tenant_id, project_id=context.project_id, run_id=projection["run_id"],
            task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=attempt.id,
            input_digest=job.request_digest, expected_binding_digest=projection["binding_digest"],
        )
        try:
            self._registry.require_run_result(
                **lookup, source_ids=projection["source_ids"], result_digest=result_digest,
                terminal_state=states[job.status],
            )
        except HubEvidenceRegistryError as exc:
            if exc.reason_code != "evidence_run_result_unavailable":
                raise
            self._registry.assignment_projection(**lookup)
            if self._current(principal, job) != attempt:
                raise SpecialistTrainingEvidenceError("specialist_evidence_completion_changed")
            self._registry.record_result(
                tenant_id=principal.tenant_id, project_id=context.project_id, run_id=projection["run_id"],
                assignment_id=job.worker_job_id, dispatch_lease_id=attempt.id,
                terminal_state=states[job.status], result_digest=result_digest,
            )
