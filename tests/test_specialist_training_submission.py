"""Bounded submission through real Hub admission, SQL and cancellation policy."""

import pytest

from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.specialist_dataset_split import split_examples
from agent.services.specialist_training_submission import (
    HubSpecialistTrainingSubmission,
    SpecialistTrainingSubmissionError,
)
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from tests.test_ml_intern_training_control_service import FakeTaskQueue, HoldingExecutor, InlineExecutor
from tests.test_specialist_dataset_admission import setup_admission
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import TINY, gate_examples


@pytest.fixture
def prepared(app, tmp_path, monkeypatch):
    del app
    admission, repository, principal = setup_admission(tmp_path)
    manifest, partitions = split_examples(gate_examples(45), seed=11)
    strategy = SpecialistTrainingTaskFamilyStrategy(GATE)
    dataset = admission.admit(
        manifest, strategy.training_records(partitions["train"]), strategy.training_records(partitions["validation"])
    )
    command = CreateTrainingJobCommand.from_mapping(
        strategy.job_request(
            dataset_id=dataset.dataset_id,
            base_model=TINY,
            method="lora",
            backend="mock",
            manifest=manifest,
            benchmark_version="bench-v1",
            extra={"expected_dataset_version": dataset.version},
        )
    )
    queue = FakeTaskQueue()
    monkeypatch.setattr("agent.services.ml_intern_training_control_service.get_task_queue_service", lambda: queue)
    return repository, principal, command, queue, tmp_path


def test_real_hub_completed_dry_run_is_replayed_without_duplicate_task(prepared):
    repository, principal, command, queue, root = prepared
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(root / "artifacts")}, repository=repository, executor=InlineExecutor()
    )
    submission = HubSpecialistTrainingSubmission(control=control, principal=principal)
    job_id = submission.run(command)
    assert control.get_job(principal, job_id)["status"] == "completed"
    assert submission.run(command) == job_id
    assert len(queue.tasks) == 1
    # A completed dry-run reference is not a manufactured model outcome.
    assert control.get_job(principal, job_id)["adapter_id"] is None


@pytest.mark.parametrize("advancing", [True, False])
def test_real_hub_wait_timeout_requests_cancellation_of_exact_submitted_job(prepared, advancing):
    repository, principal, command, queue, root = prepared
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(root / "artifacts")}, repository=repository, executor=HoldingExecutor()
    )
    now = [0.0]
    sleeps = []

    def sleep(duration):
        sleeps.append(duration)
        if advancing:
            now[0] += duration

    submission = HubSpecialistTrainingSubmission(
        control=control,
        principal=principal,
        timeout_seconds=0.2,
        poll_interval_seconds=0.1,
        clock=lambda: now[0],
        sleeper=sleep,
    )
    with pytest.raises(SpecialistTrainingSubmissionError) as error:
        submission.run(command)
    assert error.value.reason_code == "specialist_training_timeout"
    job = repository.get_job(principal, error.value.job_id)
    assert job.cancel_requested and job.status == "cancel_requested"
    assert len(queue.tasks) == 1 and sum(sleeps) <= 0.2


def test_unversioned_specialist_request_cannot_enter_submission(prepared):
    repository, principal, command, queue, root = prepared
    command.request_spec.pop("expected_dataset_version")
    control = MlInternTrainingControlService({"enabled": True}, repository=repository, executor=HoldingExecutor())
    with pytest.raises(SpecialistTrainingSubmissionError, match="admission_required"):
        HubSpecialistTrainingSubmission(control=control, principal=principal).run(command)
    assert queue.tasks == []
