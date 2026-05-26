from __future__ import annotations

from pathlib import Path

from agent.artifacts.citation_bundle_service import GoalCitationBundleService
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


def _source_descriptor(source_id: str) -> dict:
    return {
        "schema": "source_descriptor.v1",
        "source_id": source_id,
        "source_type": "keycloak_docs",
        "display_name": "Keycloak docs",
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
            "title": "Keycloak docs",
            "publisher": "example.invalid",
            "version_label": "latest",
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "license_unknown",
            "citation_text": "Keycloak docs citation",
        },
        "license": {"name": "Unknown", "ref": "license_unknown"},
        "snapshot_policy": {"immutable": True, "dedupe_by_hash": True},
        "retention_policy": {"keep_latest": 10, "max_age_days": 365},
        "extensions": {},
    }


def test_goal_citation_bundle_groups_by_source_and_snapshot(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    repository = GoalArtifactRepository(root=tmp_path)
    service = GoalArtifactService(repository=repository)
    registry.create_source(_source_descriptor("keycloak-official-docs"))
    snap = snapshots.build_snapshot(
        source_id="keycloak-official-docs",
        descriptor_hash="a" * 64,
        content_payload=[{"url": "https://example.invalid/docs"}],
        metadata_payload={"source": "fixture"},
        status="indexed",
    )
    snapshots.save_snapshot(snap)
    grant = service.create_grant(
        goal_id="goal-cite",
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-111",
            "goal_id": "goal-cite",
            "artifact_ref": f"sources:keycloak-official-docs:{snap['snapshot_id']}",
            "granted_by": "operator",
            "granted_at": "2026-05-26T00:00:00Z",
            "allowed_usages": ["read", "quote", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:1",
        },
    )
    usage = service.record_usage(
        goal_id="goal-cite",
        usage={
            "schema": "source_artifact_usage.v1",
            "usage_id": "usage-111",
            "grant_id": grant["grant_id"],
            "goal_id": "goal-cite",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "artifact_ref": grant["artifact_ref"],
            "usage_kind": "quoted",
            "used_at": "2026-05-26T00:01:00Z",
            "context_hash": "deadbeefcafefeed",
            "policy_decision_ref": "policy:1",
        },
    )
    service.record_output_artifact(
        goal_id="goal-cite",
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-111",
            "goal_id": "goal-cite",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:02:00Z",
            "input_usage_refs": [usage["usage_id"]],
            "artifact_ref": "artifacts:report:1",
            "content_hash": "b" * 64,
            "status": "created",
            "provenance_summary": "report",
        },
    )

    bundle = GoalCitationBundleService(
        goal_artifact_service=service,
        source_registry=registry,
        source_snapshots=snapshots,
    ).build_bundle(goal_id="goal-cite")
    assert bundle["goal_id"] == "goal-cite"
    assert bundle["citation_count"] == 1
    row = bundle["citations"][0]
    assert row["source_id"] == "keycloak-official-docs"
    assert row["snapshot_id"] == snap["snapshot_id"]
    assert row["output_artifact_refs"] == ["artifacts:report:1"]
    assert row["short"]
    assert row["long"]
