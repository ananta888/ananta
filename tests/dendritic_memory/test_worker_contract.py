from __future__ import annotations

import pytest

from ananta_contracts.dendritic_memory import canonical_digest
from ananta_contracts.dendritic_memory_worker import (
    DendriticCheckpointV1,
    DendriticWorkerAssignmentV1,
    DendriticWorkerResultV1,
)
from tests.dendritic_memory.helpers import assignment, config, spec


def test_worker_assignment_is_closed_fenced_and_deadline_bound() -> None:
    parsed = DendriticWorkerAssignmentV1.from_mapping(assignment())
    assert parsed.spec.digest == spec().digest
    with pytest.raises(ValueError, match="assignment_fields_invalid"):
        DendriticWorkerAssignmentV1.from_mapping({**assignment(), "worker_url": "unsafe"})
    with pytest.raises(ValueError, match="fencing_token_invalid"):
        DendriticWorkerAssignmentV1.from_mapping({**assignment(), "fencing_token": 0})


def test_checkpoint_must_bind_older_attempt_and_exact_model_configuration() -> None:
    checkpoint = DendriticCheckpointV1(
        tenant_id="tenant-1",
        run_id="dendritic-run-1",
        attempt_id="dendritic-attempt-old",
        fencing_token=1,
        spec_digest=spec().digest,
        base_model_snapshot_digest="b" * 64,
        configuration_digest=canonical_digest(config().to_dict()),
        step=4,
        payload_digest="7" * 64,
    )
    resumed = {
        **assignment(),
        "attempt_id": "dendritic-attempt-2",
        "fencing_token": 2,
        "checkpoint": checkpoint.to_dict(),
    }
    assert DendriticWorkerAssignmentV1.from_mapping(resumed).checkpoint.digest == checkpoint.digest
    with pytest.raises(ValueError, match="checkpoint_assignment_binding_invalid"):
        DendriticWorkerAssignmentV1.from_mapping({**resumed, "fencing_token": 1})


def test_worker_result_rejects_unbounded_shape_and_artifact_on_failure() -> None:
    valid = DendriticWorkerResultV1(
        run_id="dendritic-run-1",
        attempt_id="dendritic-attempt-1",
        fencing_token=1,
        state="failed",
        reason_code="dendritic_worker_execution_failed",
        event_count=2,
    ).to_dict()
    assert DendriticWorkerResultV1.from_mapping(valid).state == "failed"
    with pytest.raises(ValueError, match="result_fields_invalid"):
        DendriticWorkerResultV1.from_mapping({**valid, "raw_log": "secret"})
    with pytest.raises(ValueError, match="artifact_forbidden"):
        DendriticWorkerResultV1.from_mapping({**valid, "artifact": {"artifact_ref": "x"}})
