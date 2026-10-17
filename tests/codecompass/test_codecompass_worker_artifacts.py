"""WCRB-010: a delegated worker fetches admitted graph artifacts from the Hub, verified end to end."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.services import codecompass_worker_artifacts as wa
from agent.services.codecompass_capability_issuer import sign_capability
from agent.services.codecompass_graph_artifact_resolver import CodeCompassGraphArtifactResolver

pytestmark = pytest.mark.timeout(30)
KEY = b"k" * 32
GRAPH = json.dumps({"state": {}}).encode()
METRICS = json.dumps({"schema": "graph_visual_metrics.v1"}).encode()
G_SHA = hashlib.sha256(GRAPH).hexdigest()
M_SHA = hashlib.sha256(METRICS).hexdigest()


def _index(graph_path: Path, metrics_path: Path):
    return SimpleNamespace(id="idx-1", output_dir=None, index_metadata={"graph_artifacts": {
        "schema": "codecompass_graph_artifact_binding.v1", "graph_revision": "sha256:" + "a" * 64,
        "graph_index": {"artifact_schema": "codecompass_graph_index.v1", "filename": "cc_graph_index.json",
                        "sha256": G_SHA, "local_path": str(graph_path)},
        "visual_metrics": {"artifact_schema": "graph_visual_metrics.v1",
                           "filename": "cc_graph_index.visual_metrics.json", "sha256": M_SHA,
                           "local_path": str(metrics_path)}}})


@pytest.fixture
def hub(tmp_path):
    root = tmp_path / "hub"
    graph = root / "idx" / "run" / "cc_graph_index.json"
    metrics = graph.with_name("cc_graph_index.visual_metrics.json")
    graph.parent.mkdir(parents=True)
    graph.write_bytes(GRAPH)
    metrics.write_bytes(METRICS)
    return SimpleNamespace(root=root, index=_index(graph, metrics),
                           resolver=CodeCompassGraphArtifactResolver(artifact_root=root, allow_legacy=False))


def test_the_hub_serves_only_artifacts_of_granted_indices(hub):
    indices = {"idx-1": hub.index}.get
    granted = {"allowed_index_ids": ["idx-1"]}
    assert wa.locate_admitted_artifact(granted, G_SHA, get_index=indices, resolver=hub.resolver).name == \
        "cc_graph_index.json"
    assert wa.locate_admitted_artifact(granted, M_SHA, get_index=indices, resolver=hub.resolver).name == \
        "cc_graph_index.visual_metrics.json"
    with pytest.raises(wa.ArtifactAccessError, match="artifact_not_granted"):
        wa.locate_admitted_artifact({"allowed_index_ids": ["other"]}, G_SHA, get_index=indices, resolver=hub.resolver)
    with pytest.raises(wa.ArtifactAccessError, match="artifact_not_granted"):
        wa.locate_admitted_artifact(granted, "c" * 64, get_index=indices, resolver=hub.resolver)
    with pytest.raises(wa.ArtifactAccessError, match="digest_invalid"):
        wa.locate_admitted_artifact(granted, "../etc", get_index=indices, resolver=hub.resolver)


class _Opener:
    def __init__(self, body: bytes = GRAPH, error: Exception | None = None):
        self.body, self.error, self.requests = body, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.body)


def _materializer(tmp_path, opener):
    return wa.WorkerArtifactMaterializer(hub_url="http://hub:5000/", headers={"Authorization": "Bearer t"},
                                         capability={"audience": "http://w"}, artifact_root=tmp_path,
                                         opener=opener)


def _admitted(tmp_path) -> Path:
    return tmp_path.resolve() / "repo" / "idx" / "run" / "cc_graph_index.json"


def test_the_worker_fetches_once_to_the_admitted_path(tmp_path):
    opener = _Opener()
    path = _materializer(tmp_path, opener).materialize(G_SHA, _admitted(tmp_path))
    assert path == _admitted(tmp_path) and path.read_bytes() == GRAPH
    request = opener.requests[0]
    assert request.full_url == f"http://hub:5000/api/codecompass/worker-artifacts/{G_SHA}"
    assert wa.decode_capability(request.get_header("X-ananta-codecompass-capability")) == {"audience": "http://w"}
    _materializer(tmp_path, opener).materialize(G_SHA, _admitted(tmp_path))
    assert len(opener.requests) == 1


@pytest.mark.parametrize("opener, reason", [
    (_Opener(body=b"forged"), "digest_mismatch"),
    (_Opener(error=urllib.error.HTTPError("u", 403, "no", {}, None)), "artifact_fetch_http_403"),
    (_Opener(error=OSError("down")), "artifact_fetch_failed")])
def test_a_failed_or_forged_fetch_leaves_nothing_behind(tmp_path, opener, reason):
    with pytest.raises(wa.ArtifactAccessError, match=reason):
        _materializer(tmp_path, opener).materialize(G_SHA, _admitted(tmp_path))
    assert not any(tmp_path.rglob("*.json")) and not any(tmp_path.rglob(".fetch-*"))


def test_references_cannot_escape_the_artifact_root(tmp_path):
    root = tmp_path.resolve()
    (root / "elsewhere").mkdir()
    (root / "inside").mkdir()
    (root / "inside" / "link").symlink_to(root / "elsewhere")
    materializer = wa.WorkerArtifactMaterializer(hub_url="http://hub", headers={}, capability={},
                                                 artifact_root=root / "inside", opener=_Opener())
    for sha, path, reason in (
            ("../x", root / "inside" / "cc_graph_index.json", "reference_invalid"),
            (G_SHA, Path("inside/cc_graph_index.json"), "reference_invalid"),
            (G_SHA, root / "inside" / ".." / "cc_graph_index.json", "reference_invalid"),
            (G_SHA, root / "elsewhere" / "cc_graph_index.json", "outside_root"),
            (G_SHA, root / "inside" / "link" / "cc_graph_index.json", "outside_root")):
        with pytest.raises(wa.ArtifactAccessError, match=reason):
            materializer.materialize(sha, path)
    assert not any((root / "elsewhere").iterdir())


def test_the_worker_resolver_materializes_and_still_verifies(tmp_path):
    worker_root = tmp_path / "worker"
    # the Hub's paths, as the worker sees them: inside its own data root, but not there
    hub_run = worker_root / "idx" / "run"
    index = _index(hub_run / "cc_graph_index.json", hub_run / "cc_graph_index.visual_metrics.json")
    bodies = {G_SHA: GRAPH, M_SHA: METRICS}

    def opener(request, timeout):
        return io.BytesIO(bodies[request.full_url.rsplit("/", 1)[1]])

    resolver = CodeCompassGraphArtifactResolver(
        artifact_root=worker_root, allow_legacy=False,
        materializer=wa.WorkerArtifactMaterializer(hub_url="http://hub", headers={}, capability={},
                                                   artifact_root=worker_root.resolve(), opener=opener))
    graph, metrics = resolver.resolve_artifacts(index)
    assert graph.read_bytes() == GRAPH and metrics.read_bytes() == METRICS
    assert graph.is_relative_to(worker_root.resolve())
    # without a materializer a worker keeps failing closed as before
    with pytest.raises(ValueError, match="not_materialized"):
        CodeCompassGraphArtifactResolver(artifact_root=worker_root, allow_legacy=False).resolve_artifacts(
            _index(hub_run / "gone" / "cc_graph_index.json",
                   hub_run / "gone" / "cc_graph_index.visual_metrics.json"))


def test_only_a_worker_with_a_received_capability_gets_a_materializer(tmp_path, monkeypatch):
    from agent.services.codecompass_task_capability import task_capability_scope

    monkeypatch.setattr("agent.config.settings.role", "worker")
    monkeypatch.setattr("agent.services.worker_hub_headers.registered_worker_hub_headers", lambda: {})
    assert wa.worker_materializer(tmp_path) is None
    with task_capability_scope({"audience": "w"}):
        assert isinstance(wa.worker_materializer(tmp_path), wa.WorkerArtifactMaterializer)
        monkeypatch.setattr("agent.config.settings.role", "hub")
        assert wa.worker_materializer(tmp_path) is None


# --- Hub route ----------------------------------------------------------------------------------


@pytest.fixture
def client(hub, monkeypatch):
    from flask import g

    import agent.auth as auth
    from agent.routes import codecompass_worker_artifacts as route

    def registered(required_scope):
        assert required_scope == "codecompass.artifacts.read"
        g.service_identity = {"worker_id": "w", "worker_url": "http://worker-a:5000"}

    monkeypatch.setattr(auth, "_strict_registered_worker_bearer_error", registered)
    monkeypatch.setattr("agent.services.codecompass_capability_issuer.capability_key", lambda: KEY)
    monkeypatch.setattr("agent.services.codecompass_graph_artifact_resolver.get_codecompass_graph_artifact_resolver",
                        lambda: hub.resolver)
    monkeypatch.setattr("agent.services.repository_registry.get_repository_registry",
                        lambda: SimpleNamespace(knowledge_index_repo=SimpleNamespace(
                            get_by_id={"idx-1": hub.index}.get)))
    app = Flask(__name__)
    app.register_blueprint(route.codecompass_worker_artifacts_bp)
    return app.test_client()


def _capability(audience="http://worker-a:5000/", **overrides):
    from agent.services.codecompass_retrieval_capability_service import bind_retrieval_capability

    sealed = bind_retrieval_capability(
        {"workspace_id": "tenant:t", "repository_id": "r", "source_scope": "repo_path", "revision": "1",
         "allowed_paths": ["agent"], "allowed_index_ids": ["idx-1"], "allowed_signals": ["graph"]},
        subject_id="anna", tenant_id="t", ttl_seconds=300)
    sealed.update(task_id="t1", audience=audience, allowed_operations=[], **overrides)
    return {wa.CAPABILITY_HEADER: wa.encode_capability(sign_capability(sealed, KEY))}


def test_the_route_serves_the_verified_artifact_to_the_addressed_worker(client):
    response = client.get(f"/api/codecompass/worker-artifacts/{G_SHA}", headers=_capability())
    assert response.status_code == 200 and response.data == GRAPH
    assert response.headers["X-Artifact-SHA256"] == G_SHA


def test_the_route_refuses_other_workers_forgeries_and_foreign_artifacts(client):
    url = f"/api/codecompass/worker-artifacts/{G_SHA}"
    assert client.get(url).status_code == 403
    assert client.get(url, headers=_capability(audience="http://worker-b:5000")).status_code == 403
    forged = _capability()
    capability = wa.decode_capability(forged[wa.CAPABILITY_HEADER])
    capability["allowed_index_ids"] = ["idx-1", "idx-2"]
    assert client.get(url, headers={wa.CAPABILITY_HEADER: wa.encode_capability(capability)}).status_code == 403
    assert client.get(f"/api/codecompass/worker-artifacts/{'c' * 64}", headers=_capability()).status_code == 404


# --- wiring -------------------------------------------------------------------------------------


def test_each_codecompass_tool_call_runs_under_its_trusted_capability(monkeypatch):
    import agent.services.tools as tools
    from agent.services.codecompass_task_capability import current_task_capability

    seen = []
    monkeypatch.setattr(tools, "_dispatch_ananta_tool", lambda name, *rest: seen.append(
        (name, current_task_capability())) or {})
    trusted = {"allowed_index_ids": ["idx-1"]}
    for name in ("codecompass.architecture_overview", "repo.grep"):
        tools.execute_ananta_tool(tool_name=name, arguments={}, workspace_dir="/w", tool_call_id="c",
                                  config={"codecompass_capability": trusted})
    tools.execute_ananta_tool(tool_name="codecompass.search", arguments={}, workspace_dir="/w", tool_call_id="c")
    assert seen == [("codecompass.architecture_overview", trusted), ("repo.grep", None),
                    ("codecompass.search", None)]
    assert current_task_capability() is None


def test_the_graph_store_only_considers_the_capabilitys_indices(monkeypatch):
    from agent.services import codecompass_graph_store_resolution_service as resolution
    from agent.services.codecompass_task_capability import task_capability_scope

    assert resolution._capability_index_ids() is None
    with task_capability_scope({"allowed_index_ids": ["idx-1"]}):
        assert resolution._capability_index_ids() == {"idx-1"}
    with task_capability_scope({"allowed_index_ids": "idx-1"}):
        assert resolution._capability_index_ids() is None


# --- WCRB-013: retrieval outputs of the Hub-owned index -----------------------------------------

INDEX_RECORDS = b'{"id": "r1", "path": "a.py", "content": "hello world"}\n'
I_SHA = hashlib.sha256(INDEX_RECORDS).hexdigest()


class _Response:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body, self.headers = body, dict(headers or {})

    def read(self, size: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _IndexOpener:
    def __init__(self, body: bytes = INDEX_RECORDS, headers: dict | None = None, error: Exception | None = None):
        self.body, self.headers, self.error, self.requests = body, headers, error, []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _Response(self.body, self.headers)


@pytest.fixture
def hub_index(tmp_path):
    run = tmp_path / "hubidx" / "repo_path" / "idx-1" / "run"
    run.mkdir(parents=True)
    (run / "index.jsonl").write_bytes(INDEX_RECORDS)
    return SimpleNamespace(run=run, index=SimpleNamespace(id="idx-1", output_dir=str(run), index_metadata={}))


def test_the_hub_serves_only_granted_index_outputs(hub_index):
    index_of = {"idx-1": hub_index.index}.get
    granted = {"allowed_index_ids": ["idx-1"]}
    path, digest = wa.locate_admitted_index_output(granted, "idx-1", "index", get_index=index_of)
    assert path.name == "index.jsonl" and digest == I_SHA
    with pytest.raises(wa.ArtifactAccessError, match="artifact_not_granted"):
        wa.locate_admitted_index_output({"allowed_index_ids": ["other"]}, "idx-1", "index", get_index=index_of)
    with pytest.raises(wa.ArtifactAccessError, match="artifact_not_granted"):
        wa.locate_admitted_index_output(granted, "idx-2", "index", get_index=index_of)
    with pytest.raises(wa.ArtifactAccessError, match="artifact_role_invalid"):
        wa.locate_admitted_index_output(granted, "idx-1", "graph_index", get_index=index_of)
    with pytest.raises(wa.ArtifactAccessError, match="artifact_not_found"):
        wa.locate_admitted_index_output(granted, "idx-1", "details", get_index=index_of)


def _index_materializer(tmp_path, opener):
    return wa.WorkerIndexArtifactMaterializer(hub_url="http://hub:5000/", headers={"Authorization": "Bearer t"},
                                              capability={"audience": "http://w"}, artifact_root=tmp_path,
                                              opener=opener)


def _index_target(tmp_path) -> Path:
    return tmp_path.resolve() / "repo_path" / "idx-1" / "run" / "index.jsonl"


def test_the_worker_fetches_an_index_output_once_to_the_admitted_path(tmp_path):
    opener = _IndexOpener(headers={"X-Artifact-SHA256": I_SHA})
    path = _index_materializer(tmp_path, opener).materialize("idx-1", "index", _index_target(tmp_path))
    assert path == _index_target(tmp_path) and path.read_bytes() == INDEX_RECORDS
    assert opener.requests[0].full_url == "http://hub:5000/api/codecompass/worker-index-artifacts/idx-1/index"
    assert wa.decode_capability(opener.requests[0].get_header("X-ananta-codecompass-capability")) == {"audience": "http://w"}
    _index_materializer(tmp_path, opener).materialize("idx-1", "index", _index_target(tmp_path))
    assert len(opener.requests) == 1


@pytest.mark.parametrize("opener, reason", [
    (_IndexOpener(headers={"X-Artifact-SHA256": "a" * 64}), "digest_header_mismatch"),
    (_IndexOpener(error=urllib.error.HTTPError("u", 404, "no", {}, None)), "artifact_fetch_http_404"),
    (_IndexOpener(error=OSError("down")), "artifact_fetch_failed")])
def test_a_failed_index_fetch_leaves_nothing_behind(tmp_path, opener, reason):
    with pytest.raises(wa.ArtifactAccessError, match=reason):
        _index_materializer(tmp_path, opener).materialize("idx-1", "index", _index_target(tmp_path))
    assert not any(tmp_path.rglob("*.jsonl")) and not any(tmp_path.rglob(".fetch-*"))


def test_index_references_cannot_escape_the_artifact_root(tmp_path):
    root = tmp_path.resolve()
    (root / "inside").mkdir()
    materializer = wa.WorkerIndexArtifactMaterializer(hub_url="http://hub", headers={}, capability={},
                                                      artifact_root=root / "inside", opener=_IndexOpener())
    for index_id, path, reason in (
            ("../x", root / "inside" / "index.jsonl", "artifact_index_id_invalid"),
            ("idx-1", root / "inside" / "details.jsonl", "artifact_reference_invalid"),
            ("idx-1", root / "index.jsonl", "artifact_reference_outside_root")):
        with pytest.raises(wa.ArtifactAccessError, match=reason):
            materializer.materialize(index_id, "index", path)


def test_try_materialize_is_a_noop_off_a_delegated_worker(tmp_path, monkeypatch):
    from agent.services.codecompass_task_capability import task_capability_scope

    index = SimpleNamespace(id="idx-1", output_dir=str(tmp_path / "gone"))
    monkeypatch.setattr("agent.config.settings.role", "hub")
    with task_capability_scope({"allowed_index_ids": ["idx-1"]}):
        assert wa.try_materialize_index_outputs(index) is False


def test_try_materialize_fetches_the_granted_index_outputs(tmp_path, monkeypatch):
    from agent.services.codecompass_task_capability import task_capability_scope

    out = tmp_path / "knowledge_indices" / "repo_path" / "idx-1" / "run"
    index = SimpleNamespace(id="idx-1", output_dir=str(out))
    monkeypatch.setattr("agent.config.settings.role", "worker")
    monkeypatch.setattr("agent.config.settings.data_dir", str(tmp_path))

    def opener(request, timeout):
        if request.full_url.endswith("/index"):
            return _Response(INDEX_RECORDS, {"X-Artifact-SHA256": I_SHA})
        raise urllib.error.HTTPError("u", 404, "no", {}, None)

    monkeypatch.setattr(wa, "index_output_materializer", lambda root: wa.WorkerIndexArtifactMaterializer(
        hub_url="http://hub:5000", headers={}, capability={"audience": "w"}, artifact_root=root, opener=opener))
    with task_capability_scope({"allowed_index_ids": ["idx-1"]}):
        assert wa.try_materialize_index_outputs(index) is True
        assert (out / "index.jsonl").read_bytes() == INDEX_RECORDS
    with task_capability_scope({"allowed_index_ids": ["other"]}):
        assert wa.try_materialize_index_outputs(SimpleNamespace(id="idx-2", output_dir=str(out))) is False


@pytest.fixture
def client_index(hub_index, monkeypatch):
    from flask import g

    import agent.auth as auth
    from agent.routes import codecompass_worker_artifacts as route

    def registered(required_scope):
        g.service_identity = {"worker_id": "w", "worker_url": "http://worker-a:5000"}

    monkeypatch.setattr(auth, "_strict_registered_worker_bearer_error", registered)
    monkeypatch.setattr("agent.services.codecompass_capability_issuer.capability_key", lambda: KEY)
    monkeypatch.setattr("agent.services.repository_registry.get_repository_registry",
                        lambda: SimpleNamespace(knowledge_index_repo=SimpleNamespace(
                            get_by_id={"idx-1": hub_index.index}.get)))
    app = Flask(__name__)
    app.register_blueprint(route.codecompass_worker_artifacts_bp)
    return app.test_client()


def test_the_route_serves_a_granted_index_output(client_index):
    response = client_index.get("/api/codecompass/worker-index-artifacts/idx-1/index", headers=_capability())
    assert response.status_code == 200 and response.data == INDEX_RECORDS
    assert response.headers["X-Artifact-SHA256"] == I_SHA


def test_the_route_refuses_foreign_indices_roles_and_audiences(client_index):
    url = "/api/codecompass/worker-index-artifacts/idx-1/index"
    assert client_index.get(url).status_code == 403
    assert client_index.get(url, headers=_capability(audience="http://worker-b:5000")).status_code == 403
    assert client_index.get("/api/codecompass/worker-index-artifacts/idx-2/index",
                            headers=_capability()).status_code == 403
    assert client_index.get("/api/codecompass/worker-index-artifacts/idx-1/graph_index",
                            headers=_capability()).status_code == 403
    assert client_index.get("/api/codecompass/worker-index-artifacts/idx-1/details",
                            headers=_capability()).status_code == 404


def test_the_retrieval_service_fetches_a_missing_index_output(tmp_path, monkeypatch):
    from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService

    out = tmp_path / "knowledge_indices" / "repo_path" / "idx-1" / "run"
    index = SimpleNamespace(id="idx-1", status="completed", source_scope="repo_path", output_dir=str(out),
                            index_metadata={"codecompass_snapshot_revision": "a" * 64}, updated_at=1.0,
                            artifact_id="")
    calls: list[str] = []

    def fake_materialize(candidate):
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.jsonl").write_bytes(INDEX_RECORDS)
        calls.append(candidate.id)
        return True

    monkeypatch.setattr("agent.services.codecompass_worker_artifacts.try_materialize_index_outputs",
                        fake_materialize)
    service = KnowledgeIndexRetrievalService(
        knowledge_index_repository=SimpleNamespace(list_completed=lambda: [index]),
        knowledge_link_repository=SimpleNamespace())
    hits = service.search_records("hello world", limit=5)
    assert calls == ["idx-1"] and hits

