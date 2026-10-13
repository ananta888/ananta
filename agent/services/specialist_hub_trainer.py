"""Factory trainer adapter using Hub submission and authorized result projection."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent.db_models import MlInternDatasetDB
from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_dataset_admission import encode_specialist_partition
from agent.services.specialist_dataset_binding import specialist_dataset_binding
from agent.services.specialist_model_factory import TrainingOutcome


class SpecialistSubmissionPort(Protocol):
    def run(self, command: CreateTrainingJobCommand) -> str: ...


class SpecialistOutcomePort(Protocol):
    def read(
        self, principal: MlInternTrainingPrincipal, job_id: str, command: CreateTrainingJobCommand
    ) -> TrainingOutcome: ...


class SpecialistTrainingDatasetPort(Protocol):
    def get_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> MlInternDatasetDB | None: ...


class HubSpecialistTrainer:
    """Validate Factory inputs before delegating to existing Hub-owned jobs."""

    def __init__(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        repository: SpecialistTrainingDatasetPort,
        submission: SpecialistSubmissionPort,
        outcomes: SpecialistOutcomePort,
    ):
        self._principal = principal
        self._repository = repository
        self._submission = submission
        self._outcomes = outcomes

    def train(self, job: CreateTrainingJobCommand, records: Sequence[Mapping[str, Any]]) -> TrainingOutcome:
        spec = job.request_spec
        if job.job_type != "train_lora" or spec.get("task_family") != "specialist_decision":
            raise ValueError("specialist_trainer_command_invalid")
        dataset = self._repository.get_dataset(self._principal, str(spec.get("dataset_id") or ""))
        if (
            dataset is None
            or dataset.tenant_id != self._principal.tenant_id
            or dataset.owner_subject != self._principal.subject
        ):
            raise ValueError("specialist_trainer_dataset_scope_invalid")
        binding = specialist_dataset_binding(dataset)
        version = spec.get("expected_dataset_version")
        if type(version) is not int or version != binding["version"]:
            raise ValueError("specialist_trainer_dataset_version_changed")
        if (
            len(records) != binding["train_records"]
            or hashlib.sha256(encode_specialist_partition(records)).hexdigest() != binding["train_sha256"]
        ):
            raise ValueError("specialist_trainer_records_changed")
        # Hub admission rechecks the version; execution checks the actual stored bytes.
        job_id = self._submission.run(job)
        return self._outcomes.read(self._principal, job_id, job)
