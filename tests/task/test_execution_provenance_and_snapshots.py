from __future__ import annotations

from agent.artifacts.execution_provenance import validate_execution_provenance_payload
from agent.services.config_snapshot_service import ConfigSnapshotService
from agent.services.prompt_snapshot_service import PromptSnapshotService


def _execution_provenance() -> dict:
    return {
        "schema": "execution_provenance.v1",
        "provenance_id": "prov-1",
        "goal_id": "goal-1",
        "task_id": "task-1",
        "execution_id": "exec-1",
        "worker_id": "worker-1",
        "worker_kind": "native",
        "runtime_target_ref": {"runtime_type": "python", "location": "local"},
        "model_ref": {"provider_id": "local", "model_id": "none"},
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
        },
        "input_usage_refs": ["usage-1"],
        "output_artifact_refs": ["out-1"],
        "created_at": "2026-05-26T00:00:00Z",
    }


def test_execution_provenance_schema_accepts_valid_payload() -> None:
    assert validate_execution_provenance_payload(_execution_provenance()) == []


def test_execution_provenance_schema_rejects_missing_task_id() -> None:
    payload = _execution_provenance()
    payload.pop("task_id", None)
    errors = validate_execution_provenance_payload(payload)
    assert any("task_id" in err for err in errors)


def test_config_snapshot_hashes_are_stable_and_redacted_differs() -> None:
    service = ConfigSnapshotService()
    config = {"model": "gpt", "password": "super-secret", "nested": {"token": "abc123"}}
    first = service.build_snapshot(
        config_kind="model_config",
        source_path_or_ref="config/model.json",
        scope="goal:goal-1",
        config_payload=config,
    )
    second = service.build_snapshot(
        config_kind="model_config",
        source_path_or_ref="config/model.json",
        scope="goal:goal-1",
        config_payload=config,
    )
    assert first["config_hash"] == second["config_hash"]
    assert first["redacted_config_hash"] == second["redacted_config_hash"]
    assert first["config_hash"] != first["redacted_config_hash"]


def test_prompt_template_snapshot_hash_is_stable() -> None:
    service = PromptSnapshotService()
    first = service.build_template_snapshot(
        prompt_template_ref="prompt:system",
        template_path="prompts/system.j2",
        template_version="v1",
        template_text="Hello {{user}}",
        renderer="jinja2",
        expected_output_schema_ref="schema:answer",
    )
    second = service.build_template_snapshot(
        prompt_template_ref="prompt:system",
        template_path="prompts/system.j2",
        template_version="v1",
        template_text="Hello {{user}}",
        renderer="jinja2",
        expected_output_schema_ref="schema:answer",
    )
    assert first["template_hash"] == second["template_hash"]


def test_prompt_template_snapshot_rejects_missing_template_version() -> None:
    service = PromptSnapshotService()
    try:
        service.build_template_snapshot(
            prompt_template_ref="prompt:system",
            template_path="prompts/system.j2",
            template_version="",
            template_text="Hello {{user}}",
            renderer="jinja2",
            expected_output_schema_ref="schema:answer",
        )
    except ValueError as exc:
        assert "invalid_prompt_template_snapshot" in str(exc)
        return
    raise AssertionError("expected ValueError for missing template_version")


def test_final_prompt_record_hashes_and_default_no_raw_storage() -> None:
    service = PromptSnapshotService()
    record = service.build_final_prompt_record(
        prompt_template_ref="prompt:system",
        variables_payload={"goal": "goal-1"},
        final_prompt_text="Use API_KEY=supersecret for this run",
        context_hash="deadbeefcafebabe",
        input_usage_refs=["usage-1"],
        output_schema_ref="schema:answer",
        store_raw_prompt=False,
    )
    assert record["schema"] == "final_prompt_record.v1"
    assert record["raw_prompt_stored"] is False
    assert bool(record["final_prompt_hash"])
    assert bool(record["variables_hash"])
