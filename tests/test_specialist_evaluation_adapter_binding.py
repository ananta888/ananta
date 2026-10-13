"""Evaluation transport verifies the Factory's expected model bytes before POST."""

import pytest

from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand, MlInternTrainingContractError
from agent.services.ml_intern_training_worker_port import MlInternTrainingWorkerTransportError
from tests.test_ml_intern_training_worker_port import _EvaluationOpener, _execute, _port


def _request(**extra):
    return {
        "job_type": "evaluate_lora", "dataset_id": "dataset-one", "adapter_id": "adapter-one",
        "base_model": "local/base", "backend": "mock", "method": "lora", **extra,
    }


@pytest.mark.parametrize("field", ["expected_adapter_sha256", "expected_base_model_sha256"])
@pytest.mark.parametrize("digest", [None, True, 42, "", "a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n"])
def test_evaluation_digest_contract_is_strict(digest, field):
    with pytest.raises(MlInternTrainingContractError, match=field):
        CreateTrainingJobCommand.from_mapping(_request(**{field: digest}))


def test_digest_field_is_evaluation_only():
    with pytest.raises(MlInternTrainingContractError):
        CreateTrainingJobCommand.from_mapping(_request(job_type="train_lora", expected_adapter_sha256="a" * 64))


def test_evaluation_contract_preserves_hash():
    command = CreateTrainingJobCommand.from_mapping(_request(expected_adapter_sha256="a" * 64))
    assert command.request_spec["expected_adapter_sha256"] == "a" * 64


def test_changed_adapter_is_rejected_before_worker_post(tmp_path):
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    with pytest.raises(MlInternTrainingWorkerTransportError, match="differs from admitted bytes") as error:
        _execute(port, train, validation, extra_spec={"expected_adapter_sha256": "0" * 64})
    assert error.value.reason_code == "adapter_digest_changed"
    assert opener.envelope is None


def test_matching_adapter_can_use_existing_governed_evaluation(tmp_path):
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    adapter = next((tmp_path / "artifacts").rglob("adapter_config.json")).parent
    digest = MlInternArtifactSecurityService(storage_root=tmp_path / "artifacts").validate_adapter_tree(adapter)[
        "tree_sha256"
    ]
    result = _execute(port, train, validation, extra_spec={"expected_adapter_sha256": digest})
    assert result["status"] == "completed"
    assert opener.envelope is not None


def test_changed_base_snapshot_is_rejected_before_worker_post(tmp_path):
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation, extra_spec={"expected_base_model_sha256": "c" * 64})
    assert error.value.reason_code == "base_model_digest_changed"
    assert opener.envelope is None
