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
