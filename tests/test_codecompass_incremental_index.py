from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from worker.incremental_index.compatibility import profile_digest, profiles_share_artifact
from worker.incremental_index.coordinator import IncrementalIndexCoordinator
from worker.incremental_index.decision_engine import DecisionType, IncrementalBuildDecisionEngine
from worker.incremental_index.effective_view import overlay_records
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore
from worker.incremental_index.snapshot_diff import diff_snapshots


def _file(path: str, digest: str, outcome: str = "indexed") -> dict:
    return {
        "path": path,
        "content_sha256": digest,
        "content_state": "hashed",
        "byte_size": 10,
        "detected_type": "python",
        "support_level": "full",
        "parser_strategy": "tree_sitter",
        "extractor_id": "py",
        "extractor_version": "1",
        "outcome": outcome,
        "exclusion_reason": None,
        "diagnostics": [],
        "fallback_reason": None,
    }


def _manifest(revision: str, files: list[dict], profile_digest_value: str = "a" * 64) -> dict:
    return {
        "schema": "codecompass.snapshot_manifest.v1",
        "snapshot_revision": revision,
        "source_revision": revision[:8],
        "registry_version": "1",
        "registry_digest": "b" * 64,
        "pipeline": "codecompass",
        "profile": {},
        "profile_digest": profile_digest_value,
        "files": files,
        "required_paths": {"passed": True, "rule_count": 0, "failed_patterns": [], "rules": []},
        "budget_visibility": {},
        "silently_skipped": [],
    }


def test_changeset_id_is_deterministic() -> None:
    old = _manifest("1" * 64, [_file("a.py", "c" * 64)])
    new = _manifest("2" * 64, [_file("a.py", "d" * 64)])
    first = diff_snapshots(old, new, "ws", "repo")
    second = diff_snapshots(old, new, "ws", "repo")
    assert first.changeset_id == second.changeset_id
    assert first.file_changes[0].operation == "modify"


def test_rename_and_delete_and_unchanged() -> None:
    digest = "e" * 64
    old = _manifest("1" * 64, [_file("src/old.py", digest), _file("keep.py", "f" * 64)])
    new = _manifest("2" * 64, [_file("src/new.py", digest), _file("keep.py", "f" * 64)])
    result = diff_snapshots(old, new)
    ops = {item.operation for item in result.file_changes}
    assert "rename" in ops
    assert all(item.operation != "modify" or item.path != "keep.py" for item in result.file_changes)


def test_layer_store_dedup_and_digest_guard(tmp_path) -> None:
    store = ArtifactLayerStore(tmp_path)
    layer = {"schema": "codecompass.artifact_layer.v1", "records": [{"id": "a", "v": 1}], "snapshot_revision": "r1"}
    first_id, created = store.store_layer(layer)
    second_id, created_again = store.store_layer(layer)
    assert first_id == second_id
    assert created is True
    assert created_again is False
    loaded = store.get_layer(first_id)
    assert loaded["records"][0]["id"] == "a"


def test_head_cas_conflict(tmp_path) -> None:
    registry = LayerHeadRegistry(tmp_path)
    created = registry.create_head("default", layer_id="l1", snapshot_revision="r1")
    assert created.success
    ok = registry.update_head("default", expected_generation=1, new_layer_id="l2")
    assert ok.success
    conflict = registry.update_head("default", expected_generation=1, new_layer_id="l3")
    assert conflict.success is False
    assert conflict.error == "generation_conflict"


def test_tombstone_wins_in_effective_view() -> None:
    merged = overlay_records(
        [{"id": "n1", "path": "a.py"}],
        [{"id": "n1", "tombstone": True, "operation": "tombstone"}],
        [{"id": "n2", "path": "b.py"}],
    )
    assert [item["id"] for item in merged] == ["n2"]


def test_decision_engine_delta_vs_embedding_rebase() -> None:
    engine = IncrementalBuildDecisionEngine()
    delta = engine.decide(1, {"direct_impact": ["a.py"], "severity_score": 0.1})
    assert delta.decision_type == DecisionType.DELTA_BUILD
    rebase = engine.decide(
        1,
        {"direct_impact": ["a.py"], "severity_score": 0.1},
        build_profile_old={"embedding_profile": {"model": "a", "dimensions": 8}},
        build_profile_new={"embedding_profile": {"model": "b", "dimensions": 8}},
    )
    assert rebase.decision_type == DecisionType.ARTIFACT_KIND_REBASE


def test_coordinator_incremental_then_compact(tmp_path) -> None:
    coord = IncrementalIndexCoordinator(tmp_path)
    profile = {"profile_id": "default", "embedding_profile": {"model": "local", "dimensions": 8}}
    old = _manifest("1" * 64, [_file("a.py", "c" * 64)])
    new = _manifest("2" * 64, [_file("a.py", "d" * 64), _file("b.py", "e" * 64)])
    plan = coord.plan(old_manifest=old, new_manifest=new, profile=profile)
    applied = coord.apply(plan=plan, profile=profile)
    assert applied["status"] == "published"
    later = _manifest("3" * 64, [_file("a.py", "d" * 64)])
    plan2 = coord.plan(old_manifest=new, new_manifest=later, profile=profile, previous_profile=profile)
    applied2 = coord.apply(plan=plan2, profile=profile)
    assert applied2["status"] == "published"
    compacted = coord.compact("default", dry_run=False)
    assert compacted["status"] in {"executed", "noop", "planned"}


def test_compatible_profiles_share_keys() -> None:
    left = {"embedding_profile": {"model": "m", "dimensions": 8}, "graph_profile": {"schema": "v1"}}
    right = {"embedding_profile": {"model": "m", "dimensions": 8}, "graph_profile": {"schema": "v1"}}
    assert profiles_share_artifact(left, right, "embeddings")
    right["embedding_profile"]["model"] = "other"
    assert profiles_share_artifact(left, right, "embeddings") is False
    assert profile_digest(left) != profile_digest(right)


def test_schemas_exist() -> None:
    for name in (
        "codecompass_changeset.v1.json",
        "codecompass_artifact_layer.v1.json",
        "codecompass_layer_head.v1.json",
        "codecompass_compaction_plan.v1.json",
        "codecompass_build_profile.v1.json",
    ):
        payload = json.loads(Path("schemas/worker", name).read_text())
        jsonschema.Draft202012Validator.check_schema(payload)
