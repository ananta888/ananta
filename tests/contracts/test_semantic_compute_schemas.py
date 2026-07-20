from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    validate_capability_advertisement,
    validate_quality_contract,
    validate_task_lease,
)
from tests.semantic_compute_support import capability, compute_contract, task_lease

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("schema_path", "factory"),
    [
        ("schemas/webrtc/capability_advertisement.v1.json", capability),
        ("schemas/webrtc/compute_quality_contract.v1.json", compute_contract),
        ("schemas/webrtc/task_lease.v1.json", task_lease),
    ],
)
def test_valid_authority_contracts_match_closed_json_schemas(schema_path, factory) -> None:
    schema = json.loads((ROOT / schema_path).read_text())
    Draft202012Validator(schema).validate(factory())


def test_stale_unknown_and_oversized_capabilities_are_rejected() -> None:
    stale = capability(now_ms=1_000)
    with pytest.raises(SemanticComputeContractError, match="expired"):
        validate_capability_advertisement(stale, now_ms=61_001)
    unknown = capability()
    unknown["algorithms"] = ["peer-election-v99"]
    with pytest.raises(SemanticComputeContractError) as captured:
        validate_capability_advertisement(unknown)
    assert captured.value.reason_code == "unknown_capability"
    oversized = capability()
    oversized["max_artifact_bytes"] = 4_194_305
    with pytest.raises(SemanticComputeContractError):
        validate_capability_advertisement(oversized)


def test_impossible_budget_wrong_audience_and_nonfinite_values_are_rejected() -> None:
    impossible = task_lease()
    impossible["sequence_end"] = -1
    with pytest.raises(SemanticComputeContractError):
        validate_task_lease(impossible)
    with pytest.raises(SemanticComputeContractError) as captured:
        validate_task_lease(task_lease(), expected_audience="other")
    assert captured.value.reason_code == "wrong_audience"
    nonfinite = compute_contract()
    nonfinite["delay_ms"] = math.inf
    with pytest.raises(SemanticComputeContractError) as captured:
        validate_quality_contract(nonfinite)
    assert captured.value.reason_code == "non_finite_value"


def test_self_claim_schema_has_no_membership_scheduler_or_lease_authority() -> None:
    schema = json.loads((ROOT / "schemas/webrtc/capability_advertisement.v1.json").read_text())
    for forbidden in ("membership", "scheduler", "lease", "fencing_token", "issuer"):
        assert forbidden not in schema["properties"]
    injected = copy.deepcopy(capability())
    injected["lease"] = {"winner": "self"}
    assert list(Draft202012Validator(schema).iter_errors(injected))


def test_task_lease_accepts_safe_worker_url_and_rejects_unsafe_executor_urls() -> None:
    schema = json.loads((ROOT / "schemas/webrtc/task_lease.v1.json").read_text())
    safe = task_lease()
    safe["executor_id"] = "http://semantic-worker:5000"
    Draft202012Validator(schema).validate(safe)
    assert validate_task_lease(safe)["executor_id"] == safe["executor_id"]

    for unsafe in (
        "http://user:secret@semantic-worker:5000",
        "http://semantic-worker:5000?token=secret",
        "http://semantic-worker:5000/#fragment",
        "file:///tmp/worker.sock",
    ):
        rejected = task_lease()
        rejected["executor_id"] = unsafe
        with pytest.raises(SemanticComputeContractError, match="executor"):
            validate_task_lease(rejected)
