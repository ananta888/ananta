from __future__ import annotations

from pathlib import Path

from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


def _descriptor(source_id: str) -> dict:
    return {
        "schema": "source_descriptor.v1",
        "source_id": source_id,
        "source_type": "keycloak_docs",
        "display_name": "Keycloak Docs",
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
            "title": "Keycloak Docs",
            "publisher": "example.invalid",
            "version_label": "latest",
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "license_unknown",
            "citation_text": "Keycloak Docs",
        },
        "license": {"name": "Unknown", "ref": "license_unknown"},
        "snapshot_policy": {"immutable": True, "dedupe_by_hash": True},
        "retention_policy": {"keep_latest": 5, "max_age_days": 30},
        "extensions": {},
    }


def test_candidate_service_lists_mixed_sources_and_goal_outputs(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    registry.create_source(_descriptor("keycloak-official-docs"))
    snap = snapshots.build_snapshot(
        source_id="keycloak-official-docs",
        descriptor_hash="a" * 64,
        content_payload=[{"text": "hello"}],
        metadata_payload={"source": "fixture"},
        status="indexed",
    )
    snapshots.save_snapshot(snap)

    goal_service = GoalArtifactService(repository=GoalArtifactRepository(root=tmp_path))
    goal_service.register_output_artifacts_from_refs(
        goal_id="goal-candidates",
        task_id="task-1",
        worker_id="worker-1",
        artifact_refs=[{"kind": "patch_artifact", "artifact_id": "patch-1"}],
        input_usage_refs=[],
    )
    service = ArtifactCandidateService(
        source_registry=registry,
        source_snapshots=snapshots,
        goal_artifact_service=goal_service,
    )
    rows = service.list_candidates(goal_id="goal-candidates")
    types = {row["artifact_type"] for row in rows}
    assert "source_snapshot" in types
    assert "goal_output" in types
    assert all("default_policy_decision" in row for row in rows)


def test_candidate_service_filters_by_type_sensitivity_and_source(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    registry.create_source(_descriptor("wikipedia-source"))
    snap = snapshots.build_snapshot(
        source_id="wikipedia-source",
        descriptor_hash="b" * 64,
        content_payload=[{"text": "wiki"}],
        metadata_payload={"source": "fixture"},
        status="indexed",
    )
    snapshots.save_snapshot(snap)
    service = ArtifactCandidateService(source_registry=registry, source_snapshots=snapshots)

    by_type = service.list_candidates(goal_id="goal-x", artifact_type="source_snapshot")
    by_sensitivity = service.list_candidates(goal_id="goal-x", sensitivity="public")
    by_source = service.list_candidates(goal_id="goal-x", source_id="wikipedia-source")
    assert by_type
    assert by_sensitivity
    assert by_source
    assert all(item["artifact_type"] == "source_snapshot" for item in by_type)
    assert all(item["sensitivity"] == "public" for item in by_sensitivity)
    assert all(item["source_id"] == "wikipedia-source" for item in by_source)
