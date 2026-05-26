from __future__ import annotations

from pathlib import Path


def _grant_payload(goal_id: str = "goal-1", grant_id: str = "grant-1") -> dict:
    return {
        "schema": "source_artifact_grant.v1",
        "grant_id": grant_id,
        "goal_id": goal_id,
        "artifact_ref": "sources:keycloak:snap_1",
        "granted_by": "operator",
        "granted_at": "2026-05-26T00:00:00Z",
        "allowed_usages": ["read", "quote", "use_as_context"],
        "data_boundary": "project_private",
        "sensitivity": "internal",
        "policy_decision_ref": "policy:abc",
    }


def test_goal_artifacts_api_graph_grant_revoke_outputs_and_invalid_goal(client, admin_auth_header, monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr("agent.routes.goal_artifacts._goal_exists", lambda goal_id: goal_id != "missing-goal")

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

    sources = client.get("/goals/goal-1/artifacts/sources", headers=admin_auth_header)
    assert sources.status_code == 200
    assert len(sources.json["data"]["source_grants"]) == 1

    revoked = client.post(
        "/goals/goal-1/artifacts/sources/grant-1/revoke",
        headers=admin_auth_header,
        json={"revoke_reason": "manual"},
    )
    assert revoked.status_code == 200
    assert revoked.json["data"]["revoke_reason"] == "manual"

    outputs = client.get("/goals/goal-1/artifacts/outputs", headers=admin_auth_header)
    assert outputs.status_code == 200
    assert isinstance(outputs.json["data"]["output_artifacts"], list)

    citations = client.get("/goals/goal-1/artifacts/citations", headers=admin_auth_header)
    assert citations.status_code == 200
    assert citations.json["data"]["goal_id"] == "goal-1"
    assert "citations" in citations.json["data"]

    invalid = client.get("/goals/missing-goal/artifacts/graph", headers=admin_auth_header)
    assert invalid.status_code == 404


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
