from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.db_models import ArtifactDB
from agent.repository import artifact_repo


def _grant_payload(
    goal_id: str = "goal-1",
    grant_id: str = "grant-1",
    artifact_ref: str = "sources:keycloak:snap_1",
) -> dict:
    return {
        "schema": "source_artifact_grant.v1",
        "grant_id": grant_id,
        "goal_id": goal_id,
        "artifact_ref": artifact_ref,
        "granted_by": "operator",
        "granted_at": "2026-05-26T00:00:00Z",
        "allowed_usages": ["read", "quote", "use_as_context"],
        "data_boundary": "project_private",
        "sensitivity": "internal",
        "policy_decision_ref": "policy:abc",
    }


def _usage_payload(
    goal_id: str = "goal-1",
    *,
    grant_id: str = "grant-1",
    usage_id: str = "usage-1",
    artifact_ref: str = "sources:keycloak:snap_1",
) -> dict:
    return {
        "schema": "source_artifact_usage.v1",
        "usage_id": usage_id,
        "grant_id": grant_id,
        "goal_id": goal_id,
        "task_id": "task-1",
        "worker_id": "worker-1",
        "artifact_ref": artifact_ref,
        "usage_kind": "embedded",
        "used_at": "2026-05-26T00:01:00Z",
        "context_hash": "cafebabe00112233",
        "policy_decision_ref": "policy:abc",
    }


def _output_payload(
    goal_id: str = "goal-1",
    *,
    usage_refs: list[str],
    output_id: str = "out-1",
    artifact_ref: str = "artifacts:report:1",
    provenance_id: str | None = None,
) -> dict:
    payload = {
        "schema": "goal_output_artifact.v1",
        "output_artifact_id": output_id,
        "goal_id": goal_id,
        "task_id": "task-1",
        "worker_id": "worker-1",
        "artifact_type": "report",
        "created_at": "2026-05-26T00:02:00Z",
        "input_usage_refs": usage_refs,
        "artifact_ref": artifact_ref,
        "content_hash": "a" * 64,
        "status": "created",
        "provenance_summary": "generated from source usage",
    }
    if provenance_id:
        payload["provenance_id"] = provenance_id
    return payload


def _provenance_payload(
    *,
    goal_id: str,
    provenance_id: str,
    usage_id: str,
    output_id: str,
) -> dict:
    return {
        "schema": "execution_provenance.v1",
        "provenance_id": provenance_id,
        "goal_id": goal_id,
        "task_id": "task-visibility",
        "execution_id": f"execution-{provenance_id}",
        "worker_id": "worker-visibility",
        "worker_kind": "research",
        "runtime_target_ref": {
            "runtime_type": "container",
            "location": "container",
        },
        "model_ref": {"provider_id": "local", "model_id": "test-model"},
        "config_refs": {
            "worker_config_ref": "worker:test",
            "runtime_config_ref": "runtime:test",
            "model_config_ref": "model:test",
            "policy_config_ref": "policy:test",
        },
        "prompt_refs": {"reason_code": "test_fixture"},
        "input_usage_refs": [usage_id],
        "output_artifact_refs": [output_id],
        "created_at": "2026-08-04T00:00:00Z",
    }


def test_goal_artifacts_api_graph_grant_revoke_outputs_and_invalid_goal(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda goal_id: goal_id != "missing-goal")
    service = GoalArtifactService()

    graph = client.get("/goals/goal-1/artifacts/graph", headers=admin_auth_header)
    assert graph.status_code == 200
    assert graph.json["data"]["goal_id"] == "goal-1"

    grant = client.post(
        "/goals/goal-1/artifacts/sources/grant",
        headers=admin_auth_header,
        json=_grant_payload(),
    )
    assert grant.status_code == 201
    assert grant.json["data"]["grant_id"] == "grant-1"

    usage = service.record_usage(goal_id="goal-1", usage=_usage_payload())
    service.record_output_artifact(goal_id="goal-1", output_artifact=_output_payload(goal_id="goal-1", usage_refs=[usage["usage_id"]]))

    sources = client.get("/goals/goal-1/artifacts/sources", headers=admin_auth_header)
    assert sources.status_code == 200
    assert len(sources.json["data"]["source_grants"]) == 1
    assert len(sources.json["data"]["source_usages"]) == 1

    revoked = client.post(
        "/goals/goal-1/artifacts/sources/grant-1/revoke",
        headers=admin_auth_header,
        json={"revoke_reason": "manual"},
    )
    assert revoked.status_code == 200
    assert revoked.json["data"]["revoke_reason"] == "manual"

    with pytest.raises(GoalArtifactServiceError) as exc:
        service.record_usage(goal_id="goal-1", usage=_usage_payload(usage_id="usage-2"))
    assert exc.value.reason_code == "grant_revoked"

    outputs = client.get("/goals/goal-1/artifacts/outputs", headers=admin_auth_header)
    assert outputs.status_code == 200
    assert outputs.json["data"]["output_artifacts"][0]["input_usage_refs"] == ["usage-1"]

    citations = client.get("/goals/goal-1/artifacts/citations", headers=admin_auth_header)
    assert citations.status_code == 200
    assert citations.json["data"]["goal_id"] == "goal-1"
    assert "citations" in citations.json["data"]

    invalid = client.get("/goals/missing-goal/artifacts/graph", headers=admin_auth_header)
    assert invalid.status_code == 404


def test_goal_artifacts_api_rejects_invalid_grant_payload(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda _goal_id: True)
    payload = _grant_payload()
    payload.pop("policy_decision_ref", None)

    response = client.post("/goals/goal-1/artifacts/sources/grant", headers=admin_auth_header, json=payload)
    assert response.status_code == 400
    body = response.json if isinstance(response.json, dict) else {}
    assert "invalid_source_grant" in json.dumps(body)


def test_goal_artifacts_api_source_candidates(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings
    from agent.sources.source_registry import SourceRegistry

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda _goal_id: True)

    registry = SourceRegistry(root=tmp_path)
    registry.create_source(
        {
            "schema": "source_descriptor.v1",
            "source_id": "keycloak-official-docs",
            "source_type": "keycloak_docs",
            "display_name": "Keycloak",
            "enabled": True,
            "trust_level": "official_vendor_project",
            "fetch_source": {
                "url": "https://example.invalid/docs",
                "method": "GET",
                "refresh_interval": "24h",
                "cache_policy": "respect_http_cache_headers",
                "expected_format": "html",
            },
            "citation_source": {
                "canonical_url": "https://example.invalid/docs",
                "title": "Docs",
                "publisher": "example.invalid",
                "version_label": "latest",
                "retrieved_at": "2026-05-26T00:00:00Z",
                "license_ref": "license_unknown",
                "citation_text": "docs",
            },
            "license": {"name": "Unknown", "ref": "license_unknown"},
            "snapshot_policy": {"immutable": True, "dedupe_by_hash": True},
            "retention_policy": {"keep_latest": 5, "max_age_days": 30},
            "extensions": {},
        }
    )

    candidates = client.get(
        "/goals/goal-2/artifacts/source-candidates?artifact_type=source_snapshot&sensitivity=public&source_id=keycloak-official-docs",
        headers=admin_auth_header,
    )
    assert candidates.status_code == 200
    rows = candidates.json["data"]["candidates"]
    assert rows
    assert all(item["artifact_type"] == "source_snapshot" for item in rows)

    none = client.get(
        "/goals/goal-2/artifacts/source-candidates?artifact_type=source_snapshot&sensitivity=secret&source_id=does-not-exist",
        headers=admin_auth_header,
    )
    assert none.status_code == 200
    assert none.json["data"]["candidates"] == []


def test_goal_artifact_reads_hide_system_artifacts_and_linked_provenance(
    client,
    admin_auth_header,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent.artifacts.goal_artifact_service import GoalArtifactService
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda _goal_id: True)
    goal_id = "goal-system-artifact-visibility"
    public = artifact_repo.save(
        ArtifactDB(id="goal-public-artifact", artifact_metadata={})
    )
    hidden = artifact_repo.save(
        ArtifactDB(
            id="goal-hidden-index-output",
            artifact_metadata={
                "system_artifact_kind": "knowledge_index_worker_output"
            },
        )
    )
    service = GoalArtifactService()

    for label, artifact in (("public", public), ("hidden", hidden)):
        grant_id = f"grant-{label}"
        usage_id = f"usage-{label}"
        output_id = f"out-{label}"
        provenance_id = f"provenance-{label}"
        service.create_grant(
            goal_id=goal_id,
            grant=_grant_payload(
                goal_id=goal_id,
                grant_id=grant_id,
                artifact_ref=artifact.id,
            ),
        )
        service.record_usage(
            goal_id=goal_id,
            usage=_usage_payload(
                goal_id=goal_id,
                grant_id=grant_id,
                usage_id=usage_id,
                artifact_ref=artifact.id,
            ),
        )
        service.upsert_execution_provenance(
            goal_id=goal_id,
            provenance=_provenance_payload(
                goal_id=goal_id,
                provenance_id=provenance_id,
                usage_id=usage_id,
                output_id=output_id,
            ),
        )
        service.record_output_artifact(
            goal_id=goal_id,
            output_artifact=_output_payload(
                goal_id=goal_id,
                usage_refs=[usage_id],
                output_id=output_id,
                artifact_ref=artifact.id,
                provenance_id=provenance_id,
            ),
        )

    graph_response = client.get(
        f"/goals/{goal_id}/artifacts/graph",
        headers=admin_auth_header,
    )
    sources_response = client.get(
        f"/goals/{goal_id}/artifacts/sources",
        headers=admin_auth_header,
    )
    outputs_response = client.get(
        f"/goals/{goal_id}/artifacts/outputs",
        headers=admin_auth_header,
    )
    citations_response = client.get(
        f"/goals/{goal_id}/artifacts/citations",
        headers=admin_auth_header,
    )
    hidden_output_provenance = client.get(
        f"/goals/{goal_id}/artifacts/outputs/out-hidden/provenance",
        headers=admin_auth_header,
    )
    hidden_direct_provenance = client.get(
        f"/goals/{goal_id}/artifacts/executions/provenance-hidden",
        headers=admin_auth_header,
    )
    public_output_provenance = client.get(
        f"/goals/{goal_id}/artifacts/outputs/out-public/provenance",
        headers=admin_auth_header,
    )

    assert graph_response.status_code == 200
    graph = graph_response.json["data"]
    assert [row["grant_id"] for row in graph["source_grants"]] == [
        "grant-public"
    ]
    assert [row["usage_id"] for row in graph["source_usages"]] == [
        "usage-public"
    ]
    assert [row["output_artifact_id"] for row in graph["output_artifacts"]] == [
        "out-public"
    ]
    assert all("hidden" not in json.dumps(edge) for edge in graph["edges"])
    assert "provenance-hidden" not in json.dumps(graph["extensions"])

    assert sources_response.status_code == 200
    assert "grant-hidden" not in json.dumps(sources_response.json["data"])
    assert outputs_response.status_code == 200
    assert "out-hidden" not in json.dumps(outputs_response.json["data"])
    assert citations_response.status_code == 200
    assert hidden.id not in json.dumps(citations_response.json["data"])
    assert hidden_output_provenance.status_code == 404
    assert hidden_direct_provenance.status_code == 404
    assert public_output_provenance.status_code == 200
