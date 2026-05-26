from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.artifacts.goal_artifact_graph import validate_goal_artifact_graph_payload
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha() -> str:
    return "a" * 64


def _grant(goal_id: str, *, grant_id: str = "g01", expires_at: str | None = None, revoked_at: str | None = None) -> dict:
    payload = {
        "schema": "source_artifact_grant.v1",
        "grant_id": grant_id,
        "goal_id": goal_id,
        "artifact_ref": "sources:keycloak:snap_1",
        "granted_by": "operator",
        "granted_at": _now_iso(),
        "allowed_usages": ["read", "quote", "use_as_context"],
        "data_boundary": "project_private",
        "sensitivity": "internal",
        "policy_decision_ref": "policy:abc",
    }
    if expires_at:
        payload["expires_at"] = expires_at
    if revoked_at:
        payload["revoked_at"] = revoked_at
        payload["revoke_reason"] = "manual_revoke"
    return payload


def _usage(goal_id: str, *, usage_id: str = "u01", grant_id: str = "g01") -> dict:
    return {
        "schema": "source_artifact_usage.v1",
        "usage_id": usage_id,
        "grant_id": grant_id,
        "goal_id": goal_id,
        "task_id": "task-1",
        "worker_id": "worker-1",
        "artifact_ref": "sources:keycloak:snap_1",
        "usage_kind": "read",
        "used_at": _now_iso(),
        "context_hash": "deadbeefcafebabe",
        "prompt_ref": "prompt:1",
        "policy_decision_ref": "policy:abc",
    }


def _output(goal_id: str, *, output_id: str = "out01", input_refs: list[str] | None = None) -> dict:
    return {
        "schema": "goal_output_artifact.v1",
        "output_artifact_id": output_id,
        "goal_id": goal_id,
        "task_id": "task-1",
        "worker_id": "worker-1",
        "artifact_type": "report",
        "created_at": _now_iso(),
        "input_usage_refs": input_refs or [],
        "artifact_ref": "artifacts:report:o1",
        "content_hash": _sha(),
        "status": "created",
        "provenance_summary": "Report generated from granted source",
    }


def _service(tmp_path: Path) -> GoalArtifactService:
    repository = GoalArtifactRepository(root=tmp_path)
    return GoalArtifactService(repository=repository)


def test_goal_graph_schema_validation_valid_and_invalid(tmp_path: Path) -> None:
    service = _service(tmp_path)
    graph = service.get_goal_graph("goal-a")
    assert validate_goal_artifact_graph_payload(graph) == []
    bad = dict(graph)
    bad.pop("goal_id", None)
    errors = validate_goal_artifact_graph_payload(bad)
    assert any("goal_id" in message for message in errors)


def test_service_happy_path_with_output_provenance(tmp_path: Path) -> None:
    service = _service(tmp_path)
    goal_id = "goal-happy"
    service.create_grant(goal_id=goal_id, grant=_grant(goal_id))
    usage = service.record_usage(goal_id=goal_id, usage=_usage(goal_id))
    service.record_output_artifact(goal_id=goal_id, output_artifact=_output(goal_id, input_refs=[usage["usage_id"]]))
    graph = service.get_goal_graph(goal_id)
    assert len(graph["source_grants"]) == 1
    assert len(graph["source_usages"]) == 1
    assert len(graph["output_artifacts"]) == 1
    assert any(edge["edge_kind"] == "grant_to_usage" for edge in graph["edges"])
    assert any(edge["edge_kind"] == "usage_to_output" for edge in graph["edges"])


def test_service_rejects_usage_for_expired_grant(tmp_path: Path) -> None:
    service = _service(tmp_path)
    goal_id = "goal-expired"
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    service.create_grant(goal_id=goal_id, grant=_grant(goal_id, expires_at=expired))
    with pytest.raises(GoalArtifactServiceError) as exc:
        service.record_usage(goal_id=goal_id, usage=_usage(goal_id))
    assert exc.value.reason_code == "grant_expired"


def test_service_rejects_usage_for_revoked_grant(tmp_path: Path) -> None:
    service = _service(tmp_path)
    goal_id = "goal-revoked"
    revoked = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    service.create_grant(goal_id=goal_id, grant=_grant(goal_id, revoked_at=revoked))
    with pytest.raises(GoalArtifactServiceError) as exc:
        service.record_usage(goal_id=goal_id, usage=_usage(goal_id))
    assert exc.value.reason_code == "grant_revoked"


def test_service_rejects_usage_for_missing_grant(tmp_path: Path) -> None:
    service = _service(tmp_path)
    goal_id = "goal-missing"
    with pytest.raises(GoalArtifactServiceError) as exc:
        service.record_usage(goal_id=goal_id, usage=_usage(goal_id, grant_id="unknown-grant"))
    assert exc.value.reason_code == "missing_grant"


def test_service_requires_policy_decision_ref_on_grant(tmp_path: Path) -> None:
    service = _service(tmp_path)
    goal_id = "goal-policy"
    grant = _grant(goal_id)
    grant.pop("policy_decision_ref", None)
    with pytest.raises(GoalArtifactServiceError) as exc:
        service.create_grant(goal_id=goal_id, grant=grant)
    assert exc.value.reason_code == "invalid_source_grant"
