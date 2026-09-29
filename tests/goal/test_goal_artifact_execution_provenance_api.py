from __future__ import annotations

import json
from pathlib import Path


def _provenance_payload(*, goal_id: str, provenance_id: str, output_artifact_id: str) -> dict:
    return {
        "schema": "execution_provenance.v1",
        "provenance_id": provenance_id,
        "goal_id": goal_id,
        "task_id": "task-1",
        "execution_id": "exec-1",
        "worker_id": "worker-1",
        "worker_kind": "native",
        "runtime_target_ref": {"runtime_type": "python", "location": "local"},
        "model_ref": {"provider_id": "local", "model_id": "gpt-5.3-codex"},
        "config_refs": {
            "worker_config_ref": "cfg-worker-1",
            "runtime_config_ref": "cfg-runtime-1",
            "model_config_ref": "cfg-model-1",
            "policy_config_ref": "cfg-policy-1",
        },
        "prompt_refs": {
            "prompt_template_ref": "prompt:goal-output",
            "prompt_template_version": "v1",
            "prompt_template_hash": "a" * 64,
            "prompt_variables_hash": "b" * 64,
            "final_prompt_hash": "c" * 64,
            "raw_prompt_stored": True,
            "reason_code": "",
        },
        "input_usage_refs": [],
        "output_artifact_refs": [output_artifact_id],
        "created_at": "2026-05-26T00:02:00Z",
    }


def _seed_output_with_provenance(tmp_path: Path, monkeypatch) -> tuple[str, str, str]:
    from agent.artifacts.goal_artifact_service import GoalArtifactService
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda _goal_id: True)

    goal_id = "goal-1"
    output_id = "out-1"
    provenance_id = "prov-1"
    service = GoalArtifactService()
    service.upsert_execution_provenance(
        goal_id=goal_id,
        provenance=_provenance_payload(goal_id=goal_id, provenance_id=provenance_id, output_artifact_id=output_id),
    )
    service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": output_id,
            "goal_id": goal_id,
            "task_id": "task-1",
            "worker_id": "worker-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:03:00Z",
            "input_usage_refs": [],
            "artifact_ref": "artifacts:report:1",
            "content_hash": "d" * 64,
            "status": "created",
            "provenance_summary": "seeded output",
            "provenance_id": provenance_id,
        },
    )
    return goal_id, output_id, provenance_id


def test_output_provenance_endpoint_returns_redacted_payload(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    goal_id, output_id, _ = _seed_output_with_provenance(tmp_path, monkeypatch)

    response = client.get(f"/goals/{goal_id}/artifacts/outputs/{output_id}/provenance", headers=admin_auth_header)
    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["provenance_id"] == "prov-1"
    assert payload["prompt_refs"]["prompt_template_ref"] == "prompt:goal-output"
    assert "raw_prompt_stored" not in payload["prompt_refs"]
    assert payload["config_refs"]["model_config_ref"] == "cfg-model-1"


def test_execution_provenance_endpoint_supports_policy_blocked_raw_prompt(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    goal_id, _, provenance_id = _seed_output_with_provenance(tmp_path, monkeypatch)

    response = client.get(
        f"/goals/{goal_id}/artifacts/executions/{provenance_id}?include_raw_prompt=1",
        headers=admin_auth_header,
    )
    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["prompt_access"]["reason_code"] == "raw_prompt_access_blocked"
    assert payload["prompt_access"]["raw_prompt"] == "policy_blocked"


def test_execution_provenance_endpoint_returns_404_for_missing_provenance(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda _goal_id: True)

    response = client.get("/goals/goal-1/artifacts/executions/missing", headers=admin_auth_header)
    assert response.status_code == 404
    assert "provenance_not_found" in json.dumps(response.json)


def test_output_provenance_endpoint_returns_404_for_missing_output(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda _goal_id: True)

    response = client.get("/goals/goal-1/artifacts/outputs/missing/provenance", headers=admin_auth_header)
    assert response.status_code == 404
    assert "output_artifact_not_found" in json.dumps(response.json)
