"""Hub layer store, chunk plans and the (default-off) layer wiring."""

from __future__ import annotations

import hashlib

import pytest
from flask import Flask

from agent.bootstrap.codecompass_layers import initialize_codecompass_layers
from agent.services.codecompass_layer_hub_store import (
    ContentBlobStore,
    FileLayerDispatchRepository,
    SnapshotManifestStore,
)
from agent.services.codecompass_layer_query_backend import ChunkPlanPolicy, CodeCompassLayerQueryBackend
from worker.incremental_index.chunk_builder import ChunkLayerBuilder
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore
from worker.incremental_index.snapshot_diff import diff_snapshots

pytestmark = pytest.mark.timeout(60)
PROFILE = "ananta-project"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(files: dict[str, str], *, excluded: tuple[str, ...] = ()) -> dict:
    rows = [{"path": path, "content_sha256": sha(text), "outcome": "indexed"} for path, text in files.items()]
    rows += [{"path": path, "content_sha256": sha(path), "outcome": "excluded"} for path in excluded]
    return {"snapshot_revision": sha(repr(sorted(files.items())) + repr(excluded)), "files": rows}


A = {"a.py": "def a():\n    return 1\n", "b.md": "# B\ntext\n", "c.txt": "keep\n"}
B = {"a.py": "def a():\n    return 2\n", "c.txt": "keep\n", "d.py": "D = 4\n"}


@pytest.fixture
def backend(tmp_path):
    return CodeCompassLayerQueryBackend(
        layers=ArtifactLayerStore(tmp_path),
        heads=LayerHeadRegistry(tmp_path),
        snapshots=SnapshotManifestStore(tmp_path),
    )


def publish_base(tmp_path, files):
    """What Phase 3 will do after a Worker built the base: store layer + snapshot, create the head."""
    new = manifest(files)
    SnapshotManifestStore(tmp_path).put(new)
    changes = diff_snapshots({"snapshot_revision": "0" * 64, "files": []}, new).file_changes
    layer = ChunkLayerBuilder().build(changes=changes, content=files, prior_ids_by_path={}, parent_layer_id=None,
                                      snapshot_revision=new["snapshot_revision"], changeset_id="base")
    layer_id, _ = ArtifactLayerStore(tmp_path).store_layer(layer)
    LayerHeadRegistry(tmp_path).create_head(PROFILE, layer_id=layer_id, layer_set={"chunks": layer_id},
                                            snapshot_revision=new["snapshot_revision"])
    return layer_id, layer


# --- stores -----------------------------------------------------------------------------


def test_snapshot_store_round_trips_by_revision(tmp_path):
    store = SnapshotManifestStore(tmp_path)
    ref = store.put(manifest(A))
    assert store.get(ref)["files"][0]["path"] == "a.py"
    assert store.get("f" * 64) is None
    with pytest.raises(ValueError, match="snapshot_revision_invalid"):
        store.put({"snapshot_revision": "../x", "files": []})


def test_content_store_is_addressed_by_the_text_hash(tmp_path):
    store = ContentBlobStore(tmp_path)
    assert store.put(sha("hello"), "hello") is True and store.put(sha("hello"), "hello") is False
    assert store.get(sha("hello")) == "hello"
    assert store.missing([sha("hello"), sha("other")]) == [sha("other")]
    with pytest.raises(ValueError, match="content_sha256_mismatch"):
        store.put(sha("hello"), "tampered")


def test_dispatch_repository_round_trips_and_rejects_path_like_ids(tmp_path):
    repo = FileLayerDispatchRepository(tmp_path)
    repo.save({"task_id": "cc_layer_abc", "state": "planned"})
    assert repo.get("cc_layer_abc") == {"task_id": "cc_layer_abc", "state": "planned"}
    assert repo.get("cc_layer_missing") is None
    with pytest.raises(ValueError):
        repo.save({"task_id": "../escape"})


# --- policy and plans -------------------------------------------------------------------------


@pytest.mark.parametrize("has_head, changes, depth, compatible, expected", [
    (True, 0, 0, True, "noop"),
    (False, 0, 0, True, "base_build"),
    (True, 3, 0, False, "base_build"),
    (True, 3, 64, True, "base_build"),
    (True, 3, 5, True, "delta_build"),
])
def test_chunk_policy(has_head, changes, depth, compatible, expected):
    decision, _reason = ChunkPlanPolicy().decide(has_head=has_head, changes=changes, delta_depth=depth,
                                                 compatible=compatible)
    assert decision == expected


def test_without_a_head_the_plan_is_a_base_of_every_indexed_file(backend, tmp_path):
    new = manifest(A, excluded=("big.bin",))
    plan = backend.plan_update(profile_id=PROFILE, new_manifest=new)
    assert plan["decision"]["decision_type"] == "base_build" and plan["parent_layer_id"] is None
    assert sorted(item["path"] for item in plan["changeset"]["file_changes"]) == ["a.py", "b.md", "c.txt"]
    assert plan["content_sha256"] == sorted(sha(text) for text in A.values())
    assert plan["input_revision"] == new["snapshot_revision"] and plan["artifact_kinds"] == ["chunks"]


def test_a_commit_on_top_of_a_head_is_a_delta_with_prior_ids_of_changed_paths(backend, tmp_path):
    base_id, base = publish_base(tmp_path, A)
    new = manifest(B)
    SnapshotManifestStore(tmp_path).put(new)
    plan = backend.plan_update(profile_id=PROFILE, to_snapshot_ref=new["snapshot_revision"])
    assert plan["decision"]["decision_type"] == "delta_build"
    assert plan["parent_layer_id"] == base_id and plan["head_generation"] == 1
    operations = {item["path"]: item["operation"] for item in plan["changeset"]["file_changes"]}
    assert operations == {"a.py": "modify", "b.md": "delete", "d.py": "add"}
    prior = plan["prior_record_ids_by_path"]
    assert set(prior) == {"a.py", "b.md"}  # unchanged c.txt is not touched, d.py is new
    assert set(prior["a.py"]) == {row["id"] for row in base["records"] if row["path"] == "a.py"}
    assert plan["content_sha256"] == sorted({sha(B["a.py"]), sha(B["d.py"])})


def test_the_same_snapshot_again_is_a_noop(backend, tmp_path):
    publish_base(tmp_path, A)
    plan = backend.plan_update(profile_id=PROFILE, new_manifest=manifest(A))
    assert plan["decision"]["decision_type"] == "noop" and plan["changeset"]["file_changes"] == []


def test_a_file_no_longer_indexed_is_removed_like_a_deletion(backend, tmp_path):
    publish_base(tmp_path, A)
    plan = backend.plan_update(profile_id=PROFILE, new_manifest=manifest({"a.py": A["a.py"], "c.txt": "keep\n"},
                                                                         excluded=("b.md",)))
    assert [(item["path"], item["operation"]) for item in plan["changeset"]["file_changes"]] == [("b.md", "delete")]


def test_a_too_deep_chain_starts_a_new_full_base(tmp_path):
    publish_base(tmp_path, A)
    backend = CodeCompassLayerQueryBackend(
        layers=ArtifactLayerStore(tmp_path), heads=LayerHeadRegistry(tmp_path),
        snapshots=SnapshotManifestStore(tmp_path), policy=ChunkPlanPolicy(max_delta_depth=0),
    )
    plan = backend.plan_update(profile_id=PROFILE, new_manifest=manifest(B))
    assert plan["decision"]["decision_type"] == "base_build" and plan["parent_layer_id"] is None
    assert sorted(item["path"] for item in plan["changeset"]["file_changes"]) == ["a.py", "c.txt", "d.py"]
    assert plan["prior_record_ids_by_path"] == {}


def test_unknown_snapshot_refs_and_missing_profiles_are_errors(backend):
    with pytest.raises(KeyError):
        backend.plan_update(profile_id=PROFILE, to_snapshot_ref="e" * 64)
    with pytest.raises(ValueError, match="profile_id_required"):
        backend.plan_update(new_manifest=manifest(A))


def test_compact_plans_only_a_chain(backend, tmp_path):
    assert backend.compact(profile_id=PROFILE)["status"] == "noop"
    publish_base(tmp_path, A)
    assert backend.compact(profile_id=PROFILE)["status"] == "noop"
    assert backend.list_profiles() and backend.show_head(PROFILE)["generation"] == 1


def test_the_query_backend_never_writes(backend):
    with pytest.raises(RuntimeError, match="read_only"):
        backend.apply_update(plan={})


# --- wiring ---------------------------------------------------------------------------------


def app(role="hub"):
    flask_app = Flask(__name__)
    flask_app.config["ROLE"] = role
    return flask_app


def test_layers_stay_unavailable_by_default_and_outside_the_hub(tmp_path):
    assert initialize_codecompass_layers(app(), environ={}, data_dir=tmp_path).reason == "codecompass_layers_disabled"
    worker_app = app("worker")
    status = initialize_codecompass_layers(
        worker_app, environ={"ANANTA_CODECOMPASS_LAYERS_ENABLED": "1"}, data_dir=tmp_path
    )
    assert status.reason == "codecompass_layers_hub_role_required"
    assert "codecompass_layer_service" not in worker_app.extensions


class _Queue:
    def __init__(self):
        self.tasks = []

    def ingest_task(self, **kwargs):
        self.tasks.append(kwargs)


class _Evidence:
    def reserve(self, **_kwargs):
        return "RUN_test"

    def complete(self, **_kwargs):
        pass


def test_enabled_layers_answer_reads_and_gate_writes(tmp_path):
    hub = app()
    environ = {"ANANTA_CODECOMPASS_LAYERS_ENABLED": "1"}
    queue = _Queue()
    status = initialize_codecompass_layers(hub, environ=environ, data_dir=tmp_path, task_queue=queue,
                                           evidence=_Evidence())
    assert status.enabled and status.root == str(tmp_path / "codecompass_layers")
    service = hub.extensions["codecompass_layer_service"]
    assert service.list_profiles() == []
    plan = service.plan_update(profile_id=PROFILE, new_manifest=manifest(A))
    apply = {"plan": plan, "profile_ref": {}, "profile_id": PROFILE, "expected_generation": 0,
             "idempotency_key": "k"}
    with pytest.raises(RuntimeError, match="codecompass_layer_writes_disabled"):
        service.apply_update(**apply)
    assert queue.tasks == []
    environ["ANANTA_CODECOMPASS_LAYER_WRITES"] = "1"
    assert service.apply_update(**apply)["status"] == "queued" and len(queue.tasks) == 1
    assert "codecompass_layer_job_gateway" in hub.extensions
