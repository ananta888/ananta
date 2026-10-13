"""Hub-issued synthetic assignments cross LoRA contracts without Worker issuance."""

import copy
import json

import pytest

from agent.services.ml_intern_training_worker_port import MlInternTrainingWorkerTransportError
from tests.hub.test_hub_evidence_registry_service import _source
from tests.hub.test_hub_evidence_registry_service import service as _registry_service
from tests.test_ml_intern_training_worker_port import _EvaluationOpener, _execute, _port
from tests.worker.test_lora_training_contract_schemas import _validator
from tests.worker.test_lora_training_contracts import _evaluation_request, _training_request
from worker.training.contracts import TrainingContractError, parse_job_request


def _assignment(attempt_id):
    registry = _registry_service.__wrapped__()
    source = _source(registry, scope="test", synthetic=True)
    run = registry.reserve_run(
        tenant_id=source.tenant_id, project_id=source.project_id,
        task_id="synthetic-training-task", assignment_id="synthetic-worker-job",
        dispatch_lease_id=attempt_id, repository_revision="1" * 40, input_digest="a" * 64,
        execution_profile_digest="b" * 64, environment_digest="c" * 64,
        source_ids=[source.source_id], evidence_scope="test", synthetic=True,
        idempotency_key="synthetic-lora-reservation",
    )
    return registry.assignment_projection(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id,
        task_id=run.task_id, assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
    )


@pytest.mark.parametrize("request_factory", [_training_request, _evaluation_request])
def test_hub_issued_assignment_roundtrips_in_both_worker_contracts(request_factory):
    request = request_factory()
    projection = _assignment(request["attempt_id"])
    request["hub_evidence_assignment"] = projection
    parsed = parse_job_request(request)
    assert parsed.to_dict()["hub_evidence_assignment"] == projection
    _validator("mlintern-lora-training-request.v1.schema.json").validate(json.loads(json.dumps(parsed.to_dict())))
    original_hash = parsed.request_hash
    projection["source_ids"].clear()
    assert parsed.request_hash == original_hash  # Incoming caller mapping is not retained.
    parsed.hub_evidence_assignment["source_ids"].clear()
    with pytest.raises(TrainingContractError, match="assignment"):
        parsed.to_dict()


@pytest.mark.parametrize("mutation", ["digest", "extra", "attempt", "source", "null"])
def test_modified_or_wrong_attempt_projection_is_refused(mutation):
    request = _training_request()
    projection = _assignment("other-attempt" if mutation == "attempt" else request["attempt_id"])
    if mutation == "digest":
        projection["binding_digest"] = "e" * 64
    elif mutation == "extra":
        projection["may_promote"] = True
    elif mutation == "source":
        projection["source_ids"] = []
    elif mutation == "null":
        projection = None
    request["hub_evidence_assignment"] = projection
    with pytest.raises(TrainingContractError) as error:
        parse_job_request(request)
    assert error.value.code == "hub_evidence_assignment_invalid"


def test_hub_transport_preserves_exact_reserved_projection(tmp_path):
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    projection = _assignment("attempt-eval-1")
    _execute(port, train, validation, extra_spec={"_hub_evidence_assignment": projection})
    assert opener.envelope["hub_evidence_assignment"] == projection
    parsed = parse_job_request(opener.envelope)
    assert parsed.to_dict()["hub_evidence_assignment"] == projection


def test_hub_transport_refuses_wrong_attempt_before_post(tmp_path):
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    projection = copy.deepcopy(_assignment("another-attempt"))
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation, extra_spec={"_hub_evidence_assignment": projection})
    assert error.value.reason_code == "hub_evidence_assignment_invalid"
    assert opener.envelope is None
