"""Layer heads become searchable: FTS projection, pointer rows, retrieval, Meet scope."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.bootstrap.codecompass_layers import initialize_codecompass_layers
from agent.services.codecompass_layer_hub_store import ContentBlobStore, SnapshotManifestStore
from agent.services.codecompass_layer_publication_observers import pointer_index_id, pointer_source_id
from agent.services.codecompass_layer_search_index import LayerSearchIndex, fts_query
from agent.services.knowledge_index_consumption_policy import KnowledgeIndexConsumptionPolicy
from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
from agent.services.meet_knowledge_scope import ENV_NAME, allowed_index_ids
from ananta_contracts.codecompass_layer_job import TASK_KIND
from worker.incremental_index.layer_job_handler import CodeCompassLayerJobHandler

pytestmark = pytest.mark.timeout(60)
PROFILE = "ananta-project"
A = {
    "worker/supervisor.py": "import time\n\n\ndef supervise(run):\n    while True:\n        run()\n",
    "docs/guide.md": "# Guide\nHow the companion rejoins its room.\n",
    "agent/other.py": "def unrelated():\n    return 0\n",
}
B = {**A, "worker/supervisor.py": A["worker/supervisor.py"] + "\n\ndef backoff(delay):\n    return delay * 2\n"}
B.pop("docs/guide.md")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(files):
    return {"snapshot_revision": sha(json.dumps(files, sort_keys=True)),
            "files": [{"path": p, "content_sha256": sha(t), "byte_size": len(t), "outcome": "indexed"}
                      for p, t in files.items()]}


# --- FTS projection -----------------------------------------------------------------------


def layer(records):
    return {"records": records}


def row(record_id, path, content, *, symbol="", tombstone=False):
    if tombstone:
        return {"id": record_id, "tombstone": True, "operation": "tombstone", "path": path}
    return {"id": record_id, "path": path, "symbol": symbol, "kind": "function", "start_line": 1, "end_line": 2,
            "content": content, "content_hash": sha(content)}


def test_projection_rebuilds_then_applies_only_appended_layers(tmp_path):
    layers = {"base": layer([row("1", "a.py", "alpha beta"), row("2", "b.py", "beta gamma")]),
              "d1": layer([row("2", "b.py", "", tombstone=True), row("3", "c.py", "gamma delta")]),
              "compact": layer([row("1", "a.py", "alpha beta"), row("3", "c.py", "gamma delta")])}
    index = LayerSearchIndex(tmp_path / "p.sqlite")
    assert index.sync(generation=1, chain=["base"], load_layer=layers.get) == "rebuilt"
    assert index.sync(generation=1, chain=["base"], load_layer=layers.get) == "current"
    assert index.sync(generation=2, chain=["base", "d1"], load_layer=layers.get) == "applied"
    assert index.count() == 2 and index.search("beta", limit=5)[1] == 1
    assert index.sync(generation=3, chain=["compact"], load_layer=layers.get) == "rebuilt"
    assert index.count() == 2


def test_search_counts_every_match_and_ranks_path_and_symbol_higher(tmp_path):
    index = LayerSearchIndex(tmp_path / "p.sqlite")
    records = [row(str(n), f"misc/f{n}.py", "mentions supervise once") for n in range(30)]
    records.append(row("s", "worker/supervise.py", "the loop", symbol="supervise"))
    index.sync(generation=1, chain=["l"], load_layer={"l": layer(records)}.get)
    rows, total = index.search("supervise", limit=3)
    assert total == 31 and len(rows) == 3 and rows[0]["id"] == "s"


def test_fts_query_quotes_tokens_and_drops_syntax():
    assert fts_query('rag-helper "x" OR NEAR(a)') == '"rag" OR "helper" OR "or" OR "near"'
    assert fts_query("?? !") == ""


def test_a_missing_layer_is_an_error(tmp_path):
    with pytest.raises(LookupError):
        LayerSearchIndex(tmp_path / "p.sqlite").sync(generation=1, chain=["gone"], load_layer=lambda _id: None)


# --- consumption policy -----------------------------------------------------------------------


def test_the_policy_admits_valid_pointers_and_denies_forged_ones():
    policy = KnowledgeIndexConsumptionPolicy()
    good = SimpleNamespace(id="p", status="completed", source_scope="repo_path", index_metadata={
        "codecompass_layer_head": {"schema": "ananta.codecompass_layer_pointer.v1", "profile_id": PROFILE}})
    forged = SimpleNamespace(id="p", status="completed", source_scope="repo_path",
                             index_metadata={"codecompass_layer_head": {"schema": "other", "profile_id": PROFILE}})
    assert policy.evaluate(good).reason_code == "codecompass_layer_head_pointer"
    assert policy.evaluate(forged).allowed is False


# --- end to end: publish -> pointer -> retrieval ---------------------------------------------------


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


class PointerRepository:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, index_id):
        return self.rows.get(index_id)

    def save(self, row):
        self.rows[row.id] = row
        return row

    def list_completed(self, **_kwargs):
        return [row for row in self.rows.values() if row.status == "completed"]


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
    app.queue, app.pointers = Queue(), PointerRepository()
    initialize_codecompass_layers(
        app, environ={"ANANTA_CODECOMPASS_LAYERS_ENABLED": "1", "ANANTA_CODECOMPASS_LAYER_WRITES": "1"},
        data_dir=tmp_path, task_queue=app.queue, evidence=Evidence(), pointer_repository=app.pointers,
    )
    root = tmp_path / "codecompass_layers"
    app.snapshots, app.contents = SnapshotManifestStore(root), ContentBlobStore(root)
    return app


def publish(hub, files, key):
    snapshot = manifest(files)
    hub.snapshots.put(snapshot)
    for text in files.values():
        hub.contents.put(sha(text), text)
    service = hub.extensions["codecompass_layer_service"]
    head = service.show_head(PROFILE)
    plan = service.plan_update(profile_id=PROFILE, to_snapshot_ref=snapshot["snapshot_revision"])
    service.apply_update(plan=plan, profile_ref={"profile_id": PROFILE}, profile_id=PROFILE,
                         expected_generation=int((head or {}).get("generation") or 0), idempotency_key=key)
    task = hub.queue.tasks[-1]
    view = {"task_kind": TASK_KIND, "worker_execution_context": task["extra_fields"]["worker_execution_context"]}
    result = CodeCompassLayerJobHandler(GatewayClient(hub.extensions["codecompass_layer_job_gateway"])).execute(
        task=view, tid=task["task_id"])
    with hub.app_context():
        return service.admit_result(result)


def search(hub, query, **kwargs):
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=hub.pointers)
    with hub.app_context():
        return service.search_records_page(query, limit=5, passage_chars=400, **kwargs)


def test_a_published_head_is_searchable_with_lines_and_follows_the_next_commit(hub):
    assert publish(hub, A, "a")["status"] == "published"
    pointer = hub.pointers.get_by_id(pointer_index_id(PROFILE))
    assert pointer.index_metadata["source_id"] == pointer_source_id(PROFILE)
    assert pointer.index_metadata["head_generation"] == 1

    page = search(hub, "supervise")
    top = page["records"][0]
    assert top["path"] == "worker/supervisor.py" and top["symbol"] == "supervise"
    assert (top["passage"]["line_start"], top["passage"]["line_end"]) == (4, 6)
    assert page["total"] >= 1
    assert search(hub, "companion rejoins")["records"][0]["path"] == "docs/guide.md"

    assert publish(hub, B, "b")["generation"] == 2
    assert all(r["path"] != "docs/guide.md" for r in search(hub, "companion rejoins")["records"])
    backoff = search(hub, "backoff delay")["records"][0]
    assert backoff["symbol"] == "backoff" and backoff["passage"]["line_start"] == 9


def test_the_meet_scope_selects_the_layer_pointer_by_source_id(hub):
    publish(hub, A, "a")
    environ = {ENV_NAME: json.dumps({"*": [pointer_source_id(PROFILE)]})}
    ids = allowed_index_ids("any", lambda: hub.pointers.list_completed(), environ=environ)
    assert ids == {pointer_index_id(PROFILE)}
    assert search(hub, "supervise", index_ids=ids)["records"][0]["path"] == "worker/supervisor.py"


def test_without_layer_wiring_a_pointer_row_is_skipped(hub):
    publish(hub, A, "a")
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=hub.pointers)
    other = Flask("plain")
    with other.app_context():
        assert service.search_records_page("supervise", limit=5)["records"] == []
