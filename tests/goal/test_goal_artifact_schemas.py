from __future__ import annotations

from agent.artifacts.artifact_grants import validate_source_artifact_grant_payload
from agent.artifacts.artifact_usage import validate_source_artifact_usage_payload
from agent.artifacts.goal_artifact_graph import build_empty_goal_artifact_graph, validate_goal_artifact_graph_payload
from agent.artifacts.output_artifacts import validate_goal_output_artifact_payload


def _grant() -> dict:
    return {
        "schema": "source_artifact_grant.v1",
        "grant_id": "grant-1",
        "goal_id": "goal-1",
        "artifact_ref": "sources:keycloak:snap_1",
        "granted_by": "operator",
        "granted_at": "2026-05-26T00:00:00Z",
        "allowed_usages": ["read", "use_as_context"],
        "data_boundary": "project_private",
        "sensitivity": "internal",
        "policy_decision_ref": "policy-1",
    }


def _usage() -> dict:
    return {
        "schema": "source_artifact_usage.v1",
        "usage_id": "usage-1",
        "grant_id": "grant-1",
        "goal_id": "goal-1",
        "task_id": "task-1",
        "worker_id": "worker-1",
        "artifact_ref": "sources:keycloak:snap_1",
        "usage_kind": "embedded",
        "used_at": "2026-05-26T00:00:00Z",
        "context_hash": "deadbeef00ff11aa",
        "policy_decision_ref": "policy-1",
    }


def _output() -> dict:
    return {
        "schema": "goal_output_artifact.v1",
        "output_artifact_id": "out-1",
        "goal_id": "goal-1",
        "task_id": "task-1",
        "worker_id": "worker-1",
        "artifact_type": "report",
        "created_at": "2026-05-26T00:00:00Z",
        "input_usage_refs": ["usage-1"],
        "artifact_ref": "artifacts:report:1",
        "content_hash": "a" * 64,
        "status": "created",
        "provenance_summary": "ok",
    }


def test_goal_artifact_graph_validates_empty_builder() -> None:
    graph = build_empty_goal_artifact_graph(goal_id="goal-x")
    assert validate_goal_artifact_graph_payload(graph) == []


def test_goal_artifact_graph_rejects_missing_goal_id() -> None:
    graph = build_empty_goal_artifact_graph(goal_id="goal-x")
    graph.pop("goal_id", None)
    errors = validate_goal_artifact_graph_payload(graph)
    assert any("goal_id" in err for err in errors)


def test_goal_artifact_graph_rejects_unknown_edge_kind() -> None:
    graph = build_empty_goal_artifact_graph(goal_id="goal-x")
    graph["edges"] = [{"edge_id": "e-1", "from_ref": "grant:g1", "to_ref": "usage:u1", "edge_kind": "unknown"}]
    errors = validate_goal_artifact_graph_payload(graph)
    assert any("edge_kind" in err for err in errors)


def test_goal_artifact_graph_rejects_additional_top_level_props() -> None:
    graph = build_empty_goal_artifact_graph(goal_id="goal-x")
    graph["unknown"] = True
    errors = validate_goal_artifact_graph_payload(graph)
    assert any("Additional properties are not allowed" in err for err in errors)


def test_source_grant_accepts_valid_payload() -> None:
    assert validate_source_artifact_grant_payload(_grant()) == []


def test_source_grant_requires_policy_decision_ref() -> None:
    payload = _grant()
    payload.pop("policy_decision_ref", None)
    errors = validate_source_artifact_grant_payload(payload)
    assert any("policy_decision_ref" in err for err in errors)


def test_source_grant_rejects_invalid_usage_enum() -> None:
    payload = _grant()
    payload["allowed_usages"] = ["execute"]
    errors = validate_source_artifact_grant_payload(payload)
    assert any("allowed_usages" in err for err in errors)


def test_source_grant_rejects_invalid_data_boundary() -> None:
    payload = _grant()
    payload["data_boundary"] = "private_cloud"
    errors = validate_source_artifact_grant_payload(payload)
    assert any("data_boundary" in err for err in errors)


def test_source_grant_rejects_duplicate_usages() -> None:
    payload = _grant()
    payload["allowed_usages"] = ["read", "read"]
    errors = validate_source_artifact_grant_payload(payload)
    assert any("non-unique elements" in err for err in errors)


def test_source_usage_accepts_valid_payload() -> None:
    assert validate_source_artifact_usage_payload(_usage()) == []


def test_source_usage_requires_grant_id() -> None:
    payload = _usage()
    payload.pop("grant_id", None)
    errors = validate_source_artifact_usage_payload(payload)
    assert any("grant_id" in err for err in errors)


def test_source_usage_rejects_invalid_usage_kind() -> None:
    payload = _usage()
    payload["usage_kind"] = "quote"
    errors = validate_source_artifact_usage_payload(payload)
    assert any("usage_kind" in err for err in errors)


def test_source_usage_rejects_short_context_hash() -> None:
    payload = _usage()
    payload["context_hash"] = "short"
    errors = validate_source_artifact_usage_payload(payload)
    assert any("context_hash" in err for err in errors)


def test_source_usage_requires_preview_when_task_id_missing() -> None:
    payload = _usage()
    payload.pop("task_id", None)
    payload["usage_kind"] = "embedded"
    errors = validate_source_artifact_usage_payload(payload)
    assert any("preview" in err for err in errors)


def test_source_usage_allows_preview_without_task_id() -> None:
    payload = _usage()
    payload.pop("task_id", None)
    payload["usage_kind"] = "preview"
    assert validate_source_artifact_usage_payload(payload) == []


def test_goal_output_accepts_valid_payload() -> None:
    assert validate_goal_output_artifact_payload(_output()) == []


def test_goal_output_rejects_invalid_content_hash() -> None:
    payload = _output()
    payload["content_hash"] = "xyz"
    errors = validate_goal_output_artifact_payload(payload)
    assert any("content_hash" in err for err in errors)


def test_goal_output_rejects_invalid_status() -> None:
    payload = _output()
    payload["status"] = "pending"
    errors = validate_goal_output_artifact_payload(payload)
    assert any("status" in err for err in errors)


def test_goal_output_rejects_duplicate_input_refs() -> None:
    payload = _output()
    payload["input_usage_refs"] = ["usage-1", "usage-1"]
    errors = validate_goal_output_artifact_payload(payload)
    assert any("non-unique elements" in err for err in errors)


def test_goal_output_worker_execution_requires_provenance_id() -> None:
    payload = _output()
    payload["provenance_kind"] = "worker_execution"
    payload.pop("provenance_id", None)
    errors = validate_goal_output_artifact_payload(payload)
    assert any("provenance_id" in err for err in errors)


def test_goal_output_worker_execution_accepts_provenance_id() -> None:
    payload = _output()
    payload["provenance_kind"] = "worker_execution"
    payload["provenance_id"] = "prov-123"
    assert validate_goal_output_artifact_payload(payload) == []


def test_goal_output_accepts_planning_track_artifact_type() -> None:
    payload = _output()
    payload["artifact_type"] = "planning_track"
    assert validate_goal_output_artifact_payload(payload) == []
