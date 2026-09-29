from __future__ import annotations

from agent.artifacts.execution_provenance import validate_execution_provenance_payload


def _payload() -> dict:
    return {
        "schema": "execution_provenance.v1",
        "provenance_id": "prov-1",
        "goal_id": "goal-1",
        "task_id": "task-1",
        "execution_id": "exec-1",
        "worker_id": "worker-1",
        "worker_kind": "native",
        "runtime_target_ref": {"runtime_type": "python", "location": "local"},
        "model_ref": {"provider_id": "local", "model_id": "gpt"},
        "config_refs": {
            "worker_config_ref": "cfg-worker-1",
            "runtime_config_ref": "cfg-runtime-1",
            "model_config_ref": "cfg-model-1",
            "policy_config_ref": "cfg-policy-1",
        },
        "prompt_refs": {
            "prompt_template_ref": "prompt:default",
            "prompt_template_version": "v1",
            "prompt_template_hash": "a" * 64,
            "prompt_variables_hash": "b" * 64,
            "final_prompt_hash": "c" * 64,
            "raw_prompt_stored": False,
            "reason_code": "",
        },
        "input_usage_refs": ["usage-1"],
        "output_artifact_refs": ["out-1"],
        "created_at": "2026-05-26T00:00:00Z",
    }


def test_execution_provenance_schema_accepts_valid_payload() -> None:
    assert validate_execution_provenance_payload(_payload()) == []


def test_execution_provenance_schema_rejects_missing_required_field() -> None:
    payload = _payload()
    payload.pop("task_id", None)
    errors = validate_execution_provenance_payload(payload)
    assert any("task_id" in err for err in errors)
