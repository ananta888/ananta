"""Real admitted datasets and outcome reader; synthetic completed model artifacts."""

import pytest

from agent.services.ml_intern_training_control_service import MlInternTrainingControlService
from agent.services.specialist_dataset_split import split_examples
from agent.services.specialist_hub_trainer import HubSpecialistTrainer
from agent.services.specialist_training_submission import HubSpecialistTrainingSubmission
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from tests.test_ml_intern_training_control_service import InlineExecutor
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import gate_examples
from tests.test_specialist_training_outcome import completed as _completed


@pytest.fixture
def completed(app, tmp_path, monkeypatch):
    return _completed.__wrapped__(app, tmp_path, monkeypatch)


def _records():
    _manifest, partitions = split_examples(gate_examples(45), seed=11)
    return SpecialistTrainingTaskFamilyStrategy(GATE).training_records(partitions["train"])


def _trainer(completed, calls):
    reader, principal, job, command, _manifest, _adapter, repo = completed

    class CompletedSubmission:
        def run(self, submitted):
            assert submitted == command
            calls.append(job.id)
            return job.id

    return HubSpecialistTrainer(
        principal=principal, repository=repo, submission=CompletedSubmission(), outcomes=reader
    ), command


def test_factory_trainer_projects_actual_registered_adapter(completed):
    calls = []
    trainer, command = _trainer(completed, calls)
    outcome = trainer.train(command, _records())
    assert calls == [outcome.job_id]
    assert outcome.adapter_id == completed[2].adapter_id
    assert outcome.usage.adapter_bytes > 0


def test_real_hub_dry_run_does_not_manufacture_model_outcome(completed, tmp_path):
    reader, principal, _job, command, _manifest, _adapter, repo = completed
    control = MlInternTrainingControlService(
        {"enabled": True, "artifact_root": str(tmp_path / "dry-artifacts")},
        repository=repo,
        executor=InlineExecutor(),
    )
    trainer = HubSpecialistTrainer(
        principal=principal,
        repository=repo,
        submission=HubSpecialistTrainingSubmission(control=control, principal=principal),
        outcomes=reader,
    )
    with pytest.raises(ValueError, match="specialist_training_registry_binding_invalid"):
        trainer.train(command, _records())


@pytest.mark.parametrize("mutation", ["content", "order", "count", "version", "artifact"])
def test_invalid_inputs_never_submit_and_invalid_outcome_never_returns(completed, mutation):
    calls = []
    trainer, command = _trainer(completed, calls)
    records = _records()
    if mutation == "content":
        records = [dict(row) for row in records]
        records[0]["instruction"] = "substituted training input"
    elif mutation == "order":
        records = list(reversed(records))
    elif mutation == "count":
        records = records[:-1]
    elif mutation == "version":
        repo, principal = completed[6], completed[1]
        dataset = repo.get_dataset(principal, command.request_spec["dataset_id"])
        dataset.name = "changed"
        repo.save_dataset(dataset, expected_version=dataset.version)
    else:
        (completed[5] / "adapter_config.json").write_text('{"modified":true}')
    with pytest.raises(ValueError):
        trainer.train(command, records)
    assert len(calls) == (1 if mutation == "artifact" else 0)
