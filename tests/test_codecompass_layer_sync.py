"""Post-commit sync: client manifest from the commit tree, Hub deferral and catch-up."""

from __future__ import annotations

import hashlib
import subprocess

import pytest
from flask import Flask

from agent.bootstrap.codecompass_layers import initialize_codecompass_layers
from agent.services.codecompass_layer_sync_service import LayerSyncConflict
from ananta_contracts.codecompass_layer_job import TASK_KIND
from scripts import codecompass_layer_sync as client
from worker.incremental_index.layer_job_handler import CodeCompassLayerJobHandler

pytestmark = pytest.mark.timeout(60)
PROFILE = "ananta-project"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(files):
    rows = [{"path": p, "content_sha256": sha(t), "byte_size": len(t), "outcome": "indexed"} for p, t in files.items()]
    return {"schema": "codecompass.layer_snapshot.v1", "snapshot_revision": sha(repr(sorted(files.items()))),
            "files": rows}


class Queue:
    def __init__(self):
        self.tasks = []

    def ingest_task(self, **kwargs):
        self.tasks.append(kwargs)


class Evidence:
    def reserve(self, *, envelope, **_kwargs):
        return "RUN_" + envelope["intent_digest"][:32]

    def complete(self, **_kwargs):
        pass


class Pointers:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, index_id):
        return self.rows.get(index_id)

    def save(self, row):
        self.rows[row.id] = row


class GatewayClient:
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
    app.queue = Queue()
    initialize_codecompass_layers(
        app, environ={"ANANTA_CODECOMPASS_LAYERS_ENABLED": "1", "ANANTA_CODECOMPASS_LAYER_WRITES": "1"},
        data_dir=tmp_path, task_queue=app.queue, evidence=Evidence(), pointer_repository=Pointers(),
        task_status=lambda task_id: app.task_statuses.get(task_id, "todo"),
    )
    app.task_statuses = {}
    app.sync = app.extensions["codecompass_layer_sync_service"]
    return app


def push(hub, files):
    """What the client does: manifest, missing contents, commit."""
    accepted = hub.sync.accept_snapshot(manifest(files))
    texts = {sha(t): t for t in files.values()}
    hub.sync.accept_content({digest: texts[digest] for digest in accepted["missing_content_sha256"]})
    return accepted, hub.sync.commit(profile_id=PROFILE, snapshot_ref=accepted["snapshot_ref"], commit_sha="abc1234")


def run_worker(hub, fail=False):
    task = hub.queue.tasks[-1]
    view = {"task_kind": TASK_KIND, "worker_execution_context": task["extra_fields"]["worker_execution_context"]}
    gateway = GatewayClient(hub.extensions["codecompass_layer_job_gateway"])
    if fail:
        gateway.fetch_content = lambda *_a: {"texts": {}}
    result = CodeCompassLayerJobHandler(gateway).execute(task=view, tid=task["task_id"])
    with hub.app_context():
        return hub.extensions["codecompass_layer_service"].admit_result(result)


def test_only_missing_contents_are_requested(hub):
    first, _ = push(hub, {"a.py": "A = 1\n", "b.py": "B = 2\n"})
    assert len(first["missing_content_sha256"]) == 2
    second = hub.sync.accept_snapshot(manifest({"a.py": "A = 1\n", "c.py": "C = 3\n"}))
    assert second["missing_content_sha256"] == [sha("C = 3\n")]


def test_a_commit_during_a_build_is_deferred_and_caught_up_after_publish(hub):
    _, first = push(hub, {"a.py": "A = 1\n"})
    assert first["status"] == "queued" and first["decision"] == "base_build"
    _, second = push(hub, {"a.py": "A = 2\n", "b.py": "B = 2\n"})
    _, third = push(hub, {"a.py": "A = 3\n", "b.py": "B = 2\n"})
    assert second["status"] == third["status"] == "deferred" and len(hub.queue.tasks) == 1

    assert run_worker(hub)["status"] == "published"
    # The Hub dispatched the newest requested snapshot on its own, as one delta.
    assert len(hub.queue.tasks) == 2
    assert run_worker(hub)["generation"] == 2
    head = hub.extensions["codecompass_layer_service"].show_head(PROFILE)
    assert head["effective_source_revision"] == manifest({"a.py": "A = 3\n", "b.py": "B = 2\n"})["snapshot_revision"]
    assert len(hub.queue.tasks) == 2  # caught up: nothing more to do


def test_a_failed_build_frees_the_profile_for_the_next_commit(hub):
    push(hub, {"a.py": "A = 1\n"})
    assert run_worker(hub, fail=True)["status"] == "failed"
    _, again = push(hub, {"a.py": "A = 1\n"})
    assert again["status"] == "queued"


def test_a_commit_without_uploaded_contents_is_refused(hub):
    accepted = hub.sync.accept_snapshot(manifest({"a.py": "A = 1\n"}))
    with pytest.raises(LayerSyncConflict, match="content_missing"):
        hub.sync.commit(profile_id=PROFILE, snapshot_ref=accepted["snapshot_ref"])
    with pytest.raises(KeyError):
        hub.sync.commit(profile_id=PROFILE, snapshot_ref="f" * 64)


@pytest.mark.parametrize("files", [
    [{"path": "../etc/passwd", "content_sha256": "a" * 64}],
    [{"path": "a.py", "content_sha256": "a" * 64}, {"path": "a.py", "content_sha256": "b" * 64}],
])
def test_unsafe_or_duplicate_paths_are_rejected(hub, files):
    with pytest.raises(ValueError, match="paths_invalid"):
        hub.sync.accept_snapshot({"schema": "codecompass.layer_snapshot.v1", "snapshot_revision": "c" * 64,
                                  "files": files})


# --- client ----------------------------------------------------------------------------------


def test_the_client_reads_the_commit_tree_not_the_working_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.test")
    run("config", "user.name", "t")
    (repo / "a.py").write_text('api_key = "abcd1234efgh5678"\n')
    (repo / "blob.bin").write_bytes(b"\0\1\2")
    run("add", ".")
    run("commit", "-qm", "c1")
    (repo / "a.py").write_text("dirty = True\n")  # not committed
    monkeypatch.setattr(client, "REPO", repo)
    cache = client.BlobCache(tmp_path / "cache.json")
    manifest_, sources = client.build_manifest(client.git("rev-parse", "HEAD", cwd=repo).decode().strip(), cache)
    (entry,) = manifest_["files"]
    assert entry["path"] == "a.py"  # the binary blob is not indexed
    redacted = 'api_key = "[REDACTED]"\n'
    assert entry["content_sha256"] == sha(redacted)
    assert sources[entry["content_sha256"]][1] == "a.py"
    cache.save()
    assert client.BlobCache(tmp_path / "cache.json").entries == cache.entries


def test_client_uploads_in_bounded_batches(monkeypatch):
    texts = {f"f{n}.py": ("x = %d\n" % n) * 50 for n in range(5)}
    sources = {sha(t): (f"blob{n}", f"f{n}.py") for n, t in enumerate(texts.values())}
    blobs = {f"blob{n}": t.encode() for n, t in enumerate(texts.values())}
    monkeypatch.setattr(client, "read_blobs", lambda shas: ((s, blobs[s]) for s in shas))
    monkeypatch.setattr(client, "UPLOAD_BATCH_CHARS", 500)
    calls = []

    class FakeHub:
        def post(self, path, body):
            calls.append(len(body["texts"]))
            return {"received": len(body["texts"])}

    assert client.upload(FakeHub(), list(sources), sources) == 5
    assert len(calls) > 1 and sum(calls) == 5


def test_a_build_the_queue_already_ended_does_not_keep_the_profile_busy(hub):
    """Live regression: the autopilot failed the task outside result admission; later commits waited 2 h."""
    _, first = push(hub, {"a.py": "A = 1\n"})
    _, deferred = push(hub, {"a.py": "A = 2\n"})
    assert deferred["status"] == "deferred"
    hub.task_statuses[first["task_id"]] = "failed"
    _, again = push(hub, {"a.py": "A = 3\n"})
    assert again["status"] == "queued" and len(hub.queue.tasks) == 2
