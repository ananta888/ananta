"""Garbage collection of Hub layer storage: sweep the unreachable, keep what is needed."""

from __future__ import annotations

import os
import time

import pytest
from flask import Flask

from agent.bootstrap.codecompass_layers import initialize_codecompass_layers
from agent.services.codecompass_layer_hub_store import (
    ContentBlobStore,
    FileLayerDispatchRepository,
    SnapshotManifestStore,
)
from agent.services.codecompass_layer_storage_gc import GarbageCollectionObserver, LayerStorageGarbageCollector
from agent.services.codecompass_layer_sync_service import SyncStateStore
from tests.test_codecompass_layer_dispatch_flow import A, B, Evidence, Queue, commit, sha, work
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore

pytestmark = pytest.mark.timeout(60)
PROFILE = "ananta-project"


@pytest.fixture
def hub(tmp_path):
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.queue, app.evidence = Queue(), Evidence()
    initialize_codecompass_layers(
        app, environ={"ANANTA_CODECOMPASS_LAYERS_ENABLED": "1", "ANANTA_CODECOMPASS_LAYER_WRITES": "1"},
        data_dir=tmp_path, task_queue=app.queue, evidence=app.evidence,
    )
    app.root = tmp_path / "codecompass_layers"
    app.snapshots, app.contents = SnapshotManifestStore(app.root), ContentBlobStore(app.root)
    return app


def collector(hub, *, now=None, grace=0, history_depth=0, retention=0):
    root = hub.root
    return LayerStorageGarbageCollector(
        layers=ArtifactLayerStore(root), heads=LayerHeadRegistry(root), snapshots=SnapshotManifestStore(root),
        contents=ContentBlobStore(root), dispatches=FileLayerDispatchRepository(root),
        sync_state=SyncStateStore(root), clock=lambda: now if now is not None else time.time() + 10,
        grace_seconds=grace, history_depth=history_depth, dispatch_retention_seconds=retention,
    )


def publish(hub, files, key):
    commit(hub, files, key)
    _result, admitted = work(hub)
    assert admitted["status"] == "published"


def test_superseded_snapshots_and_contents_go_the_current_chain_stays(hub):
    publish(hub, A, "a")
    publish(hub, B, "b")
    report = collector(hub).collect(dry_run=False)
    assert report["layers"]["swept"] == 0  # base + delta are the current chain
    assert report["snapshots"]["swept"] == 1  # snapshot A is neither head nor requested
    assert hub.contents.get(sha(A["b.md"])) is None  # only snapshot A referenced it
    assert all(hub.contents.get(sha(text)) == text for text in B.values())
    assert report["dispatches"]["swept"] == 2  # both published, retention 0


def test_a_replaced_chain_is_swept_outside_the_history_window(hub, monkeypatch):
    publish(hub, A, "a")
    publish(hub, B, "b")
    heads = LayerHeadRegistry(hub.root)
    head = heads.get_head(PROFILE)
    chain = [*head["base_layer_set"].values(), *[d["chunks"] for d in head["ordered_delta_sets"]]]
    # Simulate a new base (compaction by rebuild) replacing the chain.
    new_base = ArtifactLayerStore(hub.root).store_layer({"schema": "x", "records": [], "snapshot_revision": "n"})[0]
    heads.update_head(PROFILE, expected_generation=head["generation"], new_layer_id=new_base,
                      new_layer_set={"chunks": new_base}, append_delta=False, replace_artifact_kinds=["chunks"])
    kept_by_history = collector(hub, history_depth=10).collect(dry_run=True)
    assert kept_by_history["layers"]["swept"] == 0
    swept = collector(hub, history_depth=1).collect(dry_run=False)
    assert swept["layers"]["swept"] == len(chain)
    assert all(not ArtifactLayerStore(hub.root).has_layer(layer_id) for layer_id in chain)


def test_the_grace_period_protects_fresh_uploads_and_dry_run_deletes_nothing(hub):
    publish(hub, A, "a")
    publish(hub, B, "b")
    orphan = ArtifactLayerStore(hub.root).store_layer({"schema": "x", "records": [], "snapshot_revision": "o"})[0]
    assert collector(hub, grace=3600, now=time.time()).collect(dry_run=False)["layers"]["swept"] == 0
    dry = collector(hub).collect(dry_run=True)
    assert dry["layers"]["swept"] == 1 and ArtifactLayerStore(hub.root).has_layer(orphan)


def test_an_active_job_keeps_its_parent_snapshot_and_contents(hub):
    publish(hub, A, "a")
    commit(hub, B, "b")  # dispatched, not yet built
    report = collector(hub).collect(dry_run=False)
    assert report["dispatches"]["kept"] >= 1
    assert all(hub.contents.get(sha(text)) == text for text in B.values())
    gateway = hub.extensions["codecompass_layer_job_gateway"]
    task_id = hub.queue.tasks[-1]["task_id"]
    assert gateway.content_part(task_id, 0)["texts"]  # the worker can still fetch its job
    _result, admitted = work(hub)
    assert admitted["status"] == "published"


def test_ended_dispatch_records_are_kept_for_the_retention_period(hub):
    publish(hub, A, "a")
    report = collector(hub, retention=7 * 24 * 3600).collect(dry_run=False)
    assert report["dispatches"] == {"kept": 1, "swept": 0}
    path = next((hub.root / "dispatches").glob("*.json"))
    old = time.time() - 8 * 24 * 3600
    os.utime(path, (old, old))
    assert collector(hub, retention=7 * 24 * 3600).collect(dry_run=False)["dispatches"]["swept"] == 1


def test_the_observer_collects_at_most_once_per_interval():
    runs = []

    class Collector:
        def collect(self, *, dry_run):
            runs.append(dry_run)
            return {}

    now = [1000.0]
    observer = GarbageCollectionObserver(Collector(), min_interval=3600, clock=lambda: now[0], start=lambda f: f())
    observer.published({})
    observer.published({})
    now[0] += 3601
    observer.published({})
    assert runs == [False, False]


def test_the_hub_wires_the_collector_and_the_gc_route(hub):
    assert isinstance(hub.extensions["codecompass_layer_gc"], LayerStorageGarbageCollector)
