"""Technical evaluation composition with real scoped SQL and prediction artifacts."""

from dataclasses import replace

import pytest

from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_dataset_binding import BINDING_KEY, specialist_dataset_binding
from agent.services.specialist_dataset_split import split_examples
from agent.services.specialist_evaluation_receipt_reader import SpecialistEvaluationReceiptReader
from agent.services.specialist_hub_evaluation import HubSpecialistEvaluation
from agent.services.specialist_training_submission import HubSpecialistJobSubmission
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from tests.test_specialist_job_predictions import admitted as _admitted
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import gate_examples


@pytest.fixture
def evaluation(app, tmp_path):
    reader, repo, principal, job, dataset, *_ = _admitted.__wrapped__(app, tmp_path)
    manifest, _partitions = split_examples(gate_examples(45), seed=11)
    spec = SpecialistTrainingTaskFamilyStrategy(GATE).job_request(
        dataset_id=dataset.id, base_model="local/base", method="lora", backend="mock",
        manifest=manifest, benchmark_version="bench-v1",
        extra={"expected_dataset_version": dataset.version},
    )
    spec.update(job_type="evaluate_lora", adapter_id="adapter-one", expected_adapter_sha256="a" * 64,
                scorer_name="specialist_decision")
    command = CreateTrainingJobCommand.from_mapping(spec)
    pin = job.request_spec[BINDING_KEY]
    job.request_spec = {**command.request_spec, BINDING_KEY: pin}
    job.adapter_id = "adapter-one"
    job = repo.save_job(job, expected_version=job.version)
    calls = []

    class CompletedControl:
        def create_job(self, received_principal, payload, *, idempotency_key):
            assert received_principal == principal and payload == command.request_spec
            calls.append(idempotency_key)
            return {"id": job.id}, False

        def get_job(self, received_principal, job_id):
            assert received_principal == principal and job_id == job.id
            current = repo.get_job(principal, job_id)
            return {"id": current.id, "status": current.status}

        def cancel_job(self, *args, **kwargs):
            pytest.fail("completed evaluation must not be cancelled")

    runner = HubSpecialistEvaluation(
        principal=principal, repository=repo,
        submission=HubSpecialistJobSubmission(control=CompletedControl(), principal=principal,
                                             job_type="evaluate_lora"),
        predictions=reader,
    )
    return runner, command, repo, principal, job, calls


def test_evaluation_composes_bound_submission_and_all_raw_predictions(evaluation):
    runner, command, _repo, _principal, _job, calls = evaluation
    outputs = runner.evaluate(command)
    assert len(outputs.adapter) == 25 and len(outputs.base) == 25
    assert len(calls) == 1 and calls[0].startswith("specialist-evaluate-")


def test_evaluation_receipt_uses_actual_completed_sql_job_and_attempt(evaluation):
    runner, command, _repo, _principal, job, _calls = evaluation
    result = runner.evaluate_result(command)
    assert result.receipt.job_id == job.id and result.receipt.job_version == job.version
    assert result.receipt.attempt_id == job.active_attempt_id
    assert result.receipt.dataset_id == job.dataset_id
    assert result.receipt.adapter_id == job.adapter_id
    assert result.receipt.request_digest == job.request_digest
    assert len(result.outputs.adapter) == 25


@pytest.mark.parametrize("mutation", [None, "job", "attempt", "adapter", "dataset", "request"])
def test_receipt_revalidation_checks_current_job_and_actual_prediction_bytes(evaluation, mutation):
    runner, command, repo, principal, job, _calls = evaluation
    result = runner.evaluate_result(command)
    reader = SpecialistEvaluationReceiptReader(repository=repo, predictions=runner._predictions)
    receipt = result.receipt
    if mutation == "job":
        current = repo.get_job(principal, job.id)
        current.error_code = "changed-after-receipt"
        repo.save_job(current, expected_version=current.version)
    elif mutation == "attempt":
        receipt = replace(receipt, attempt_id="another-attempt")
    elif mutation == "adapter":
        receipt = replace(receipt, adapter_sha256="b" * 64)
    elif mutation == "dataset":
        receipt = replace(receipt, dataset_version=receipt.dataset_version + 1)
    elif mutation == "request":
        receipt = replace(receipt, request_digest="f" * 64)
    if mutation is None:
        assert reader.read(principal, receipt) == result.outputs
    else:
        with pytest.raises(ValueError, match="specialist_evaluation_receipt_stale_or_mismatched"):
            reader.read(principal, receipt)


@pytest.mark.parametrize("requested_role", ["validation", "holdout"])
def test_receipt_role_must_match_persistent_evaluation_dataset_purpose(evaluation, requested_role):
    runner, command, repo, principal, job, _calls = evaluation
    dataset = repo.get_dataset(principal, job.dataset_id)
    dataset.split_manifest = {
        "execution_purpose": "specialist_evaluation", "evaluation_partition": "validation",
        "specialist_manifest_digest": "c" * 64, "holdout_digest": "d" * 64,
    }
    dataset = repo.save_dataset(dataset, expected_version=dataset.version)
    command.request_spec["expected_dataset_version"] = dataset.version
    job.request_spec["expected_dataset_version"] = dataset.version
    job.request_spec[BINDING_KEY] = specialist_dataset_binding(dataset)
    job = repo.save_job(job, expected_version=job.version)
    result = runner.evaluate_result(command)
    reader = SpecialistEvaluationReceiptReader(repository=repo, predictions=runner._predictions)
    arguments = {"partition": requested_role, "manifest_digest": "c" * 64, "holdout_digest": "d" * 64}
    if requested_role == "validation":
        assert reader.read(principal, result.receipt, **arguments) == result.outputs
    else:
        with pytest.raises(ValueError, match="specialist_evaluation_receipt_partition_mismatch"):
            reader.read(principal, result.receipt, **arguments)


@pytest.mark.parametrize("mutation", ["adapter", "digest", "version"])
def test_completed_other_evaluation_cannot_substitute_outputs(evaluation, mutation):
    runner, command, repo, principal, job, _calls = evaluation
    if mutation == "adapter":
        job.adapter_id = "other-adapter"
    elif mutation == "digest":
        job.request_spec["expected_adapter_sha256"] = "b" * 64
    else:
        job.request_spec["expected_dataset_version"] += 1
    repo.save_job(job, expected_version=job.version)
    with pytest.raises(ValueError, match="specialist_evaluation_job_binding_invalid"):
        runner.evaluate(command)


def test_unbound_evaluation_never_submits(evaluation):
    runner, command, _repo, _principal, _job, calls = evaluation
    command.request_spec.pop("expected_adapter_sha256")
    with pytest.raises(ValueError, match="specialist_evaluation_admission_required"):
        runner.evaluate(command)
    assert calls == []
