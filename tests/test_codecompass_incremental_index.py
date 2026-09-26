from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from worker.incremental_index.compatibility import profile_digest, profiles_share_artifact
from worker.incremental_index.coordinator import IncrementalIndexCoordinator
from worker.incremental_index.decision_engine import DecisionType, IncrementalBuildDecisionEngine
from worker.incremental_index.compaction import CompactionPlanner
from worker.incremental_index.effective_view import LayeredEffectiveViewResolver, overlay_records
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
    import hashlib

    stable = [_file("m%d.py" % n, hashlib.sha256(b"m%d" % n).hexdigest()) for n in range(20)]
    old = _manifest("1" * 64, [_file("a.py", "c" * 64), *stable])
    new = _manifest("2" * 64, [_file("a.py", "d" * 64), _file("b.py", "e" * 64), *stable])
    base = coord.plan(old_manifest=_manifest("0" * 64, []), new_manifest=old, profile=profile)
    assert coord.apply(plan=base, profile=profile)["status"] == "published"
    plan = coord.plan(old_manifest=old, new_manifest=new, profile=profile, previous_profile=profile)
    assert plan["decision"]["decision_type"] == "delta_build"
    applied = coord.apply(plan=plan, profile=profile)
    assert applied["status"] == "published"
    later = _manifest("3" * 64, [_file("a.py", "d" * 64), *stable])
    plan2 = coord.plan(old_manifest=new, new_manifest=later, profile=profile, previous_profile=profile)
    assert plan2["decision"]["decision_type"] == "delta_build"
    applied2 = coord.apply(plan=plan2, profile=profile)
    assert applied2["status"] == "published"
    assert len(coord.heads.get_head("default")["ordered_delta_sets"]) == 2
    resolver = LayeredEffectiveViewResolver(coord.store, coord.heads)
    before = resolver.resolve_effective_view("default")
    compacted = coord.compact("default", dry_run=False)
    assert compacted["status"] == "executed", compacted
    after = resolver.resolve_effective_view("default")
    # Compaction is lossless: same records, same content, one layer per kind.
    assert {key: item.content_hash for key, item in after.artifacts.items()} == {
        key: item.content_hash for key, item in before.artifacts.items()
    }
    assert coord.heads.get_head("default")["ordered_delta_sets"] == []
    for layer_id in compacted["layers"].values():
        layer = coord.store.get_layer(layer_id)
        assert layer["parent_layer_id"] is None and len(layer["content_digest"]) == 64


def test_small_change_in_a_large_snapshot_is_a_delta_without_a_symbol_graph() -> None:
    from worker.incremental_index.dependency_impact import DependencyImpactAnalyzer

    impact = DependencyImpactAnalyzer().analyze_impact(["a.py"], "cs", universe_size=100)
    assert impact.severity_score == 0.01 and impact.recommended_action == "delta_build"
    # Without the snapshot size the old behaviour (everything changed) stays.
    assert DependencyImpactAnalyzer().analyze_impact(["a.py"], "cs").severity_score == 1.0


def test_compaction_plan_serializes() -> None:
    plan = CompactionPlanner().create_plan("default", delta_ids=["a", "b"])
    payload = plan.to_dict()
    assert payload["schema"] == "codecompass.compaction_plan.v1"
    assert payload["candidates"][0]["layer_ids"] == ["a", "b"]


def test_store_verified_blob_recomputes_the_layer_address(tmp_path) -> None:
    import gzip

    store = ArtifactLayerStore(tmp_path)
    layer = {"schema": "codecompass.artifact_layer.v1", "records": [{"id": "r1", "text": "x"}]}
    layer_id = ArtifactLayerStore.compute_layer_id(layer)
    blob = gzip.compress(json.dumps({**layer, "layer_id": layer_id}).encode("utf-8"))
    assert store.store_verified_blob(blob, expected_layer_id=layer_id) == (layer_id, True)
    assert store.store_verified_blob(blob) == (layer_id, False)
    tampered = gzip.compress(json.dumps({**layer, "records": [], "layer_id": layer_id}).encode("utf-8"))
    try:
        store.store_verified_blob(tampered, expected_layer_id=layer_id)
    except ValueError as error:
        assert str(error) == "digest_mismatch"
    else:
        raise AssertionError("a tampered layer must be rejected")


def test_a_stale_head_lock_from_a_crashed_writer_is_recovered(tmp_path) -> None:
    import os
    import time

    registry = LayerHeadRegistry(tmp_path)
    assert registry.create_head("p", layer_id="l1", snapshot_revision="r1").success
    lock = registry._lock_path("p")
    lock.write_text("123 0")
    # A fresh foreign lock still blocks the writer ...
    assert registry.update_head("p", expected_generation=1, new_layer_id="l2").error == "lock_unavailable"
    # ... an old one is a crashed writer and is taken over.
    old = time.time() - LayerHeadRegistry.STALE_LOCK_SECONDS - 5
    os.utime(lock, (old, old))
    assert registry.update_head("p", expected_generation=1, new_layer_id="l2").success
    assert not lock.exists()


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
