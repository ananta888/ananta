"""Factory composition with real evaluation datasets and synthetic model execution."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt, SpecialistEvaluationResult
from agent.services.specialist_model_factory import SpecialistModelFactory
from agent.services.specialist_partition_predictor import HubSpecialistPartitionPredictor
from agent.services.specialist_prediction_artifact import SpecialistPredictionOutputs
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from tests.test_specialist_dataset_admission import setup_admission
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import MID, POLICY, TINY, LookupTrainer, gate_examples, plan_for


@pytest.mark.parametrize("truncate", [False, True])
@pytest.mark.parametrize("receipt_mismatch", [False, True])
def test_factory_uses_real_partition_datasets_and_requires_full_predictions(app, tmp_path, truncate, receipt_mismatch):
    del app
    admission, repo, principal = setup_admission(tmp_path)
    trainer = LookupTrainer({TINY: {}, MID: {}})
    commands = []

    class SyntheticTrainer:
        def train(self, job, records):
            return replace(trainer.train(job, records), adapter_id=f"adapter-{len(trainer.jobs)}")

    class SyntheticEvaluation:
        def evaluate_result(self, command):
            commands.append(command)
            dataset = repo.get_dataset(principal, command.dataset_id)
            assert dataset.split_manifest["execution_purpose"] == "specialist_evaluation"
            records = [json.loads(line) for line in Path(dataset.validation_storage_ref).read_text().splitlines()]
            outputs = tuple(row["output"] for row in records)
            if truncate:
                outputs = outputs[:-1]
            return SpecialistEvaluationResult(
                SpecialistPredictionOutputs(base=outputs, adapter=outputs),
                SpecialistEvaluationReceipt(
                    job_id=f"synthetic-evaluation-{len(commands)}", job_version=1,
                    attempt_id=f"synthetic-attempt-{len(commands)}", dataset_id=dataset.id,
                    dataset_version=dataset.version + (1 if receipt_mismatch else 0),
                    adapter_id=command.request_spec["adapter_id"],
                    adapter_sha256=command.request_spec["expected_adapter_sha256"],
                    base_model_sha256=command.request_spec["expected_base_model_sha256"], request_digest="a" * 64,
                ),
            )

    class SyntheticRegistration:
        def register(self, winner, decision):
            return winner.outcome.adapter_id, "evaluated"

    predictor = HubSpecialistPartitionPredictor(
        strategy=SpecialistTrainingTaskFamilyStrategy(GATE), datasets=admission, evaluation=SyntheticEvaluation()
    )
    factory = SpecialistModelFactory(
        GATE, trainer=SyntheticTrainer(), partition_predictor=predictor,
        registry=MlInternAdapterRegistryService(tmp_path / "registry.json"), policy=POLICY,
        benchmark_version="code-risk-bench-v1", dataset_admission=admission,
        candidate_registration=SyntheticRegistration(),
    )
    examples = gate_examples(45)
    if truncate or receipt_mismatch:
        reason = (
            "specialist_predictor_output_count_mismatch" if truncate
            else "specialist_partition_evaluation_receipt_mismatch"
        )
        with pytest.raises(ValueError, match=reason):
            factory.run_cycle(examples, version="v1", plan_candidates=plan_for(examples, 11), seed=11)
        assert len(commands) == 1
        return
    cycle = factory.run_cycle(examples, version="v1", plan_candidates=plan_for(examples, 11), seed=11)
    assert cycle.winner is not None
    assert cycle.registry_status == "evaluated"
    assert cycle.errors[cycle.winner.candidate_id] == ("specialist_production_promotion_pending",)
    exported = cycle.to_mapping()["evaluations"]
    assert len(commands) == 2 * len(trainer.jobs)
    eval_ids = {command.dataset_id for command in commands}
    assert len(eval_ids) == 2
    assert eval_ids.isdisjoint({job.dataset_id for job in trainer.jobs})
    for index, outcome in enumerate(cycle.candidates):
        assert [role for role, _receipt in outcome.evaluations] == ["validation", "holdout"]
        assert [receipt.job_id for _role, receipt in outcome.evaluations] == [
            f"synthetic-evaluation-{2 * index + 1}", f"synthetic-evaluation-{2 * index + 2}"
        ]
        assert exported[outcome.candidate_id]["holdout"]["job_id"] == f"synthetic-evaluation-{2 * index + 2}"
        for command in commands[2 * index:2 * index + 2]:
            assert command.request_spec["adapter_id"] == outcome.outcome.adapter_id
            assert command.request_spec["expected_adapter_sha256"] == outcome.outcome.adapter_digest
            assert command.request_spec["expected_base_model_sha256"] == outcome.outcome.base_model_digest
    for job in trainer.jobs:
        assert repo.get_dataset(principal, job.dataset_id).version == job.request_spec["expected_dataset_version"]
