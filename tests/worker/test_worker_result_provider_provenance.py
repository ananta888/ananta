"""Optional provider provenance keeps the Worker result contract closed."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


@pytest.mark.parametrize("provider_hash,valid", [("native", True), ("a" * 64, True), ("invented", False)])
def test_worker_result_accepts_only_defined_provider_provenance(provider_hash, valid):
    payload = {
        "schema": "worker_execution_result.v1", "task_id": "synthetic-task", "trace_id": "synthetic-trace",
        "status": "completed", "artifacts": [], "model_metadata": {"provider_hash": provider_hash},
    }
    path = Path(__file__).resolve().parents[2] / "schemas/worker/worker_execution_result.v1.json"
    validator = Draft202012Validator(json.loads(path.read_text()))
    assert validator.is_valid(payload) is valid
    payload["model_metadata"]["unregistered_field"] = "synthetic"
    assert not validator.is_valid(payload)
