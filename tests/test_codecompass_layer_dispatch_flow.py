"""Hub -> Worker -> Hub for CodeCompass chunk layers, headless and in memory.

The Worker handler talks to the Hub's job gateway through an in-memory
client; queue and evidence registry are deterministic doubles.
"""

from __future__ import annotations

import gzip
import hashlib
import json

import pytest
from flask import Flask

from agent.bootstrap.codecompass_layers import initialize_codecompass_layers
from agent.services._task_scoped_forwarding import _accept_codecompass_layer_result
from agent.services.codecompass_layer_hub_store import ContentBlobStore, SnapshotManifestStore
from agent.services.workflow_worker_service_auth import CODECOMPASS_LAYER_JOB_SCOPE
from ananta_contracts.codecompass_layer_job import CONTEXT_KEY, TASK_KIND, WORKER_SCOPE, partition_by_size
from worker.incremental_index.chunk_builder import ChunkLayerBuilder
from worker.incremental_index.effective_view import LayeredEffectiveViewResolver, overlay_records
from worker.incremental_index.layer_job_handler import CodeCompassLayerJobHandler
from worker.incremental_index.snapshot_diff import diff_snapshots

pytestmark = pytest.mark.timeout(60)
PROFILE = "ananta-project"
A = {"a.py": "def a():\n    return 1\n\n\nclass K:\n    pass\n", "b.md": "# B\ntext\n", "c.txt": "keep\n"}
B = {"a.py": "def a():\n    return 2\n\n\nclass K:\n    pass\n", "c.txt": "keep\n", "d.py": "D = 4\n"}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(files):
    rows = [{"path": p, "content_sha256": sha(t), "byte_size": len(t.encode()), "outcome": "indexed"}
            for p, t in files.items()]
    return {"snapshot_revision": sha(json.dumps(files, sort_keys=True)), "files": rows}


class Queue:
    def __init__(self):
        self.tasks = []

    def ingest_task(self, **kwargs):
        self.tasks.append(kwargs)


class Evidence:
    def __init__(self):
        self.reserved, self.completed = [], []

    def reserve(self, *, envelope, assignment_id, dispatch_lease_id):
        self.reserved.append(envelope["task_id"])
        return "RUN_" + envelope["intent_digest"][:32]

    def complete(self, **kwargs):
        self.completed.append(kwargs)


class GatewayClient:
    """The Worker's Hub client, wired straight to the gateway (no HTTP)."""

    def __init__(self, gateway):
        self.gateway = gateway

    def fetch_spec(self, task_id):
        return self.gateway.spec(task_id)

    def fetch_content(self, task_id, part):
        return self.gateway.content_part(task_id, part)

    def upload_layer(self, task_id, blob):
        return self.gateway.receive_layer(task_id, blob)


@pytest.fixture
def hub(tmp_path):
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    queue, evidence = Queue(), Evidence()
    environ = {"ANANTA_CODECOMPASS_LAYERS_ENABLED": "1", "ANANTA_CODECOMPASS_LAYER_WRITES": "1"}
    initialize_codecompass_layers(app, environ=environ, data_dir=tmp_path, task_queue=queue, evidence=evidence)
    root = tmp_path / "codecompass_layers"
    app.queue, app.evidence = queue, evidence
    app.snapshots, app.contents = SnapshotManifestStore(root), ContentBlobStore(root)
    return app


def commit(hub, files, key):
    """What the post-commit sync will do: upload the snapshot and contents, then apply."""
    snapshot = manifest(files)
    hub.snapshots.put(snapshot)
    for text in files.values():
        hub.contents.put(sha(text), text)
    service = hub.extensions["codecompass_layer_service"]
    head = service.show_head(PROFILE)
    plan = service.plan_update(profile_id=PROFILE, to_snapshot_ref=snapshot["snapshot_revision"])
    queued = service.apply_update(plan=plan, profile_ref={"profile_id": PROFILE}, profile_id=PROFILE,
                                  expected_generation=int((head or {}).get("generation") or 0), idempotency_key=key)
    return plan, queued


def work(hub, index=-1):
    """A Worker executes the queued task and the Hub admits the forwarded result."""
    task = hub.queue.tasks[index]
    task_view = {"task_kind": task["extra_fields"]["task_kind"],
                 "worker_execution_context": task["extra_fields"]["worker_execution_context"]}
    handler = CodeCompassLayerJobHandler(GatewayClient(hub.extensions["codecompass_layer_job_gateway"]))
    result = handler.execute(task=task_view, tid=task["task_id"])
    with hub.app_context():
        admitted = _accept_codecompass_layer_result(tid=task["task_id"], task=task_view, response=result)
    return result, admitted[TASK_KIND]


def effective(hub):
    service = hub.extensions["codecompass_layer_service"]
    backend = service._backend._query
    view = LayeredEffectiveViewResolver(backend._layers, backend._heads).resolve_effective_view(PROFILE)
    return sorted((key, item.content_hash) for key, item in view.artifacts.items())


def full_build(files):
    changes = diff_snapshots({"snapshot_revision": "0" * 64, "files": []}, manifest(files)).file_changes
    layer = ChunkLayerBuilder().build(changes=changes, content=files, prior_ids_by_path={}, parent_layer_id=None,
                                      snapshot_revision="x", changeset_id="x")
    return sorted((row["id"], row["content_hash"]) for row in overlay_records(layer["records"]))


def test_the_worker_scope_is_one_contract():
    assert WORKER_SCOPE == CODECOMPASS_LAYER_JOB_SCOPE


def test_base_then_delta_publish_through_the_worker(hub):
    plan, queued = commit(hub, A, "commit-a")
    assert queued["status"] == "queued" and plan["decision"]["decision_type"] == "base_build"
    task = hub.queue.tasks[0]
    ticket = task["extra_fields"]["worker_execution_context"][CONTEXT_KEY]
    assert task["extra_fields"]["task_kind"] == TASK_KIND and set(ticket) == {
        "schema", "task_id", "assignment_id", "dispatch_lease_id", "intent_digest", "run_id"}
    # The task carries no file content, paths or record ids: those stay in the Hub.
    rendered = json.dumps(task)
    assert all(path not in rendered for path in A) and "prior_record_ids" not in rendered

    result, admitted = work(hub)
    assert result["status"] == "completed" and admitted["status"] == "published"
    assert admitted["generation"] == 1 and admitted["run_id"].startswith("RUN_")
    assert effective(hub) == full_build(A)

    plan_b, _ = commit(hub, B, "commit-b")
    assert plan_b["decision"]["decision_type"] == "delta_build"
    _result, admitted_b = work(hub)
    assert admitted_b["status"] == "published" and admitted_b["generation"] == 2
    head = hub.extensions["codecompass_layer_service"].show_head(PROFILE)
    assert len(head["ordered_delta_sets"]) == 1
    assert effective(hub) == full_build(B)
    assert [item["succeeded"] for item in hub.evidence.completed] == [True, True]


def test_replaying_the_same_commit_is_idempotent(hub):
    commit(hub, A, "commit-a")
    _result, first = work(hub)
    _plan, again = commit(hub, A, "commit-a")
    assert again["status"] == "noop" and len(hub.queue.tasks) == 1
    task = hub.queue.tasks[0]
    view = {"task_kind": TASK_KIND, "worker_execution_context": task["extra_fields"]["worker_execution_context"]}
    handler = CodeCompassLayerJobHandler(GatewayClient(hub.extensions["codecompass_layer_job_gateway"]))
    # The job is no longer active once published: a late re-execution cannot fetch it.
    assert handler.execute(task=view, tid=task["task_id"])["status"] == "failed"


def test_a_worker_failure_ends_the_run_failed_and_keeps_the_head(hub):
    commit(hub, A, "commit-a")
    work(hub)
    commit(hub, B, "commit-b")
    hub.contents._root.joinpath(sha(B["d.py"])[:2], sha(B["d.py"]) + ".gz").unlink()
    result, admitted = work(hub)
    assert result["status"] == "failed" and admitted["status"] == "failed"
    assert hub.evidence.completed[-1]["succeeded"] is False
    assert hub.extensions["codecompass_layer_service"].show_head(PROFILE)["generation"] == 1


def test_a_head_that_moved_since_the_plan_is_a_conflict(hub):
    commit(hub, A, "commit-a")
    work(hub)
    commit(hub, B, "commit-b")  # planned against generation 1
    commit(hub, {**B, "e.py": "E = 1\n"}, "commit-c")  # also planned against generation 1
    _r, first = work(hub, index=1)
    _r, second = work(hub, index=2)
    assert first["status"] == "published"
    assert second["status"] == "failed" and second["reason_code"] == "codecompass_layer_head_generation_conflict"


def test_an_upload_for_another_parent_is_rejected_and_not_kept(hub):
    commit(hub, A, "commit-a")
    work(hub)
    commit(hub, B, "commit-b")
    gateway = hub.extensions["codecompass_layer_job_gateway"]
    task_id = hub.queue.tasks[1]["task_id"]
    spec = gateway.spec(task_id)
    forged = ChunkLayerBuilder().build(changes=[], content={}, prior_ids_by_path={}, parent_layer_id=None,
                                       snapshot_revision=spec["input_revision"], changeset_id="forged")
    before = len(hub.extensions["codecompass_layer_service"]._backend._query._layers.list_layers())
    with pytest.raises(ValueError, match="upload_binding_invalid"):
        gateway.receive_layer(task_id, gzip.compress(json.dumps(forged).encode()))
    assert len(hub.extensions["codecompass_layer_service"]._backend._query._layers.list_layers()) == before


def test_a_result_for_another_task_is_rejected():
    task = {"task_kind": TASK_KIND}
    admitted = _accept_codecompass_layer_result(tid="cc_layer_" + "a" * 32, task=task,
                                                response={"task_id": "cc_layer_" + "b" * 32})
    assert admitted[TASK_KIND]["reason_code"] == "codecompass_layer_result_task_mismatch"
    assert _accept_codecompass_layer_result(tid="x", task={"task_kind": "other"}, response={}) == {}


def test_content_parts_are_bounded_and_deterministic():
    parts = partition_by_size([("c", 5), ("a", 5), ("b", 5), ("big", 50)], max_chars=10)
    assert parts == [["a", "b"], ["big"], ["c"]]
