"""Tenant-scoped consumption of completed Hub specialist evaluation jobs."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from agent.db_models import MlInternDatasetDB, MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.ports.specialist_prediction_storage import SpecialistPredictionStoragePort
from agent.services.ml_intern_training_artifact_binding import MlInternTrainingArtifactBinding
from agent.services.specialist_dataset_binding import BINDING_KEY, require_specialist_dataset_binding
from agent.services.specialist_prediction_artifact import (
    MAX_BYTES,
    MAX_LINE_BYTES,
    SpecialistPredictionArtifactError,
    SpecialistPredictionOutputs,
    read_specialist_predictions,
)
from agent.services.unsloth_storage_governance_service import tenant_scope_digest


class SpecialistJobReadPort(Protocol):
    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> MlInternTrainingJobDB | None: ...
    def get_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> MlInternDatasetDB | None: ...
    def get_attempt(self, attempt_id: str) -> MlInternTrainingAttemptDB | None: ...


class SpecialistJobPredictionService:
    """Authorize the job, pin the dataset bytes, then consume immutable result bytes."""

    def __init__(self, *, repository: SpecialistJobReadPort, storage: SpecialistPredictionStoragePort):
        self._repository = repository
        self._storage = storage

    def read(self, principal: MlInternTrainingPrincipal, job_id: str) -> SpecialistPredictionOutputs:
        job = self._repository.get_job(principal, job_id)
        if (
            job is None
            or job.tenant_id != principal.tenant_id
            or job.owner_subject != principal.subject
            or job.status != "completed"
            or job.job_type != "evaluate_lora"
            or job.request_spec.get("task_family") != "specialist_decision"
            or job.request_spec.get("scorer_name") != "specialist_decision"
        ):
            raise SpecialistPredictionArtifactError("specialist_prediction_job_unavailable")
        attempt = self._repository.get_attempt(str(job.active_attempt_id or ""))
        if (
            attempt is None
            or attempt.id != job.active_attempt_id
            or attempt.job_id != job.id
            or attempt.tenant_id != principal.tenant_id
            or attempt.owner_subject != principal.subject
            or attempt.status != "completed"
        ):
            raise SpecialistPredictionArtifactError("specialist_prediction_attempt_invalid")
        dataset = self._repository.get_dataset(principal, str(job.dataset_id or ""))
        if (
            dataset is None
            or dataset.id != job.dataset_id
            or dataset.tenant_id != principal.tenant_id
            or dataset.owner_subject != principal.subject
            or not dataset.validation_storage_ref
        ):
            raise SpecialistPredictionArtifactError("specialist_prediction_dataset_unavailable")
        require_specialist_dataset_binding(job.request_spec, dataset)
        binding = job.request_spec[BINDING_KEY]
        records = []
        digest = hashlib.sha256()
        total = 0
        with self._storage.open_dataset(dataset.validation_storage_ref) as stream:
            while line := stream.readline(MAX_LINE_BYTES + 1):
                total += len(line)
                if total > MAX_BYTES or len(line) > MAX_LINE_BYTES:
                    raise SpecialistPredictionArtifactError("specialist_prediction_dataset_quota_exceeded")
                digest.update(line)
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise SpecialistPredictionArtifactError("specialist_prediction_dataset_json_invalid") from exc
                if not isinstance(record, dict) or len(records) >= binding["validation_records"]:
                    raise SpecialistPredictionArtifactError("specialist_prediction_dataset_coverage_invalid")
                records.append(record)
        if digest.hexdigest() != binding["validation_sha256"] or len(records) != binding["validation_records"]:
            raise SpecialistPredictionArtifactError("specialist_prediction_dataset_hash_mismatch")
        artifacts = (job.result_summary or {}).get("artifacts")
        matches = (
            [
                item
                for item in artifacts
                if isinstance(item, dict) and item.get("name") == "specialist_predictions.jsonl"
            ]
            if isinstance(artifacts, list)
            else []
        )
        if len(matches) != 1:
            raise SpecialistPredictionArtifactError("specialist_prediction_artifact_unavailable")
        metadata = matches[0]
        storage = MlInternTrainingArtifactBinding(tenant_scope_digest(principal), job.id, attempt.id)
        relative = storage.relative_directory("artifacts") + "/specialist_predictions.jsonl"
        with self._storage.open_artifact(relative) as stream:
            outputs = read_specialist_predictions(
                stream,
                admitted_sha256=metadata.get("sha256"),
                admitted_size_bytes=metadata.get("size_bytes"),
                expected_records=records,
            )
        # Recheck mutable control-plane projections before returning any output.
        current = self._repository.get_job(principal, job_id)
        current_dataset = self._repository.get_dataset(principal, dataset.id)
        current_attempt = self._repository.get_attempt(attempt.id)
        if (
            current is None
            or current.version != job.version
            or current.status != "completed"
            or current.active_attempt_id != attempt.id
            or current_dataset is None
            or current_attempt is None
            or current_attempt.version != attempt.version
            or current_attempt.status != "completed"
        ):
            raise SpecialistPredictionArtifactError("specialist_prediction_job_changed")
        require_specialist_dataset_binding(job.request_spec, current_dataset)
        return outputs
