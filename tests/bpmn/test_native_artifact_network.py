"""Real localhost HTTP publication and credential-boundary regressions."""

import hashlib
import io
import threading
import urllib.error
import urllib.parse
from contextlib import contextmanager

import pytest
from flask import redirect
from werkzeug.serving import make_server

from ananta_contracts.native_artifact_transfer import MAX_RECEIPT_HTTP_BYTES, NativeArtifactBytes
from tests.bpmn.test_native_artifact_admission import ingress, set_result_artifact  # noqa: F401
from tests.bpmn.test_native_artifact_http import TOKEN, http  # noqa: F401
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission, complete  # noqa: F401
from worker.runtime.native_graph.artifact_http import NativeArtifactHttpTransport
from worker.runtime.native_graph.artifact_publication import NativeArtifactPublisher
from worker.runtime.workflow_hub_http import _DenyHubRedirect


@pytest.fixture
def network(http):  # noqa: F811
    try:
        server = make_server("127.0.0.1", 0, http.app, threaded=True)
    except PermissionError:
        pytest.skip("sandbox forbids local TCP sockets; real network admission remains unverified")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = NativeArtifactHttpTransport(
        hub_url=f"http://127.0.0.1:{server.server_port}", bearer_token=TOKEN,
        worker_id="worker-1", worker_url="http://worker-1:5000", endpoint_prefix="", timeout_seconds=3,
    )
    try:
        yield http, transport
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive(), "native_artifact_test_server_shutdown_timeout"


def test_real_network_publication_then_task_completion(network):
    endpoint, transport = network
    content = endpoint.ingress.content
    references = NativeArtifactPublisher(transport=transport).publish(
        command=endpoint.ingress.native.command, hub_task_id=endpoint.ingress.native.task_id, project_id=None,
        artifacts=(NativeArtifactBytes("report", "report.txt", "text/plain", content,
                                       hashlib.sha256(content).hexdigest()),),
    )
    set_result_artifact(endpoint.ingress, references["report"])
    assert complete(endpoint.ingress.native).status == "completed"


def test_network_redirect_never_forwards_worker_credentials(network, monkeypatch):
    endpoint, transport = network
    calls = []
    endpoint.app.add_url_rule("/credential-sentinel", view_func=lambda: calls.append(True) or "unexpected")
    monkeypatch.setitem(endpoint.app.view_functions, "native_artifact_ingress.native_artifact_ingress",
                        lambda **kwargs: redirect("/credential-sentinel", code=307))
    with pytest.raises(ValueError, match="http_redirect_denied"):
        transport.request(method="GET", path=endpoint.url)
    assert calls == []


@pytest.mark.parametrize("response,reason", [
    ("not-json", "http_response_invalid"),
    ("x" * (MAX_RECEIPT_HTTP_BYTES + 1), "http_response_too_large"),
], ids=["invalid-json", "oversized-response"])
def test_network_response_is_bounded_and_structured(network, monkeypatch, response, reason):
    endpoint, transport = network
    monkeypatch.setitem(endpoint.app.view_functions, "native_artifact_ingress.native_artifact_ingress",
                        lambda **kwargs: response)
    with pytest.raises(ValueError, match=reason):
        transport.request(method="GET", path=endpoint.url)


@pytest.mark.parametrize("method,path", [
    ("DELETE", "/internal/tasks/task/native-artifacts"),
    ("GET", "https://foreign.example/"), ("POST", "/internal/tasks/task/native-artifacts?foreign=1"),
    ("GET", "/internal/tasks/task/native-artifacts/../../admin"),
])
def test_transport_target_cannot_escape_artifact_scope(method, path):
    transport = NativeArtifactHttpTransport(
        hub_url="http://hub:5000", bearer_token=TOKEN, worker_id="worker-1", worker_url="http://worker-1:5000",
    )
    with pytest.raises(ValueError, match="http_target_invalid"):
        transport.request(method=method, path=path)


@pytest.mark.parametrize("url", ["http://user:secret@hub", "http://hub?secret", "file:///tmp/hub", "http://hub:bad"])
def test_configured_hub_target_is_closed(url):
    with pytest.raises(ValueError, match="hub_url_invalid"):
        NativeArtifactHttpTransport(hub_url=url, bearer_token=TOKEN, worker_id="worker", worker_url="http://worker")


def test_environment_transport_requires_file_managed_worker_identity(tmp_path):
    token_file = tmp_path / "worker-token"
    token_file.write_text(TOKEN)
    token_file.chmod(0o600)
    transport = NativeArtifactHttpTransport.from_environment({
        "ANANTA_WORKFLOW_HUB_URL": "http://hub:5000", "ANANTA_WORKFLOW_HUB_TOKEN_FILE": str(token_file),
        "AGENT_NAME": "worker-1", "AGENT_URL": "http://worker-1:5000",
    })
    assert isinstance(transport, NativeArtifactHttpTransport)
    assert NativeArtifactHttpTransport.from_environment({}) is None


def test_http_encoding_through_real_flask_admission_without_socket(http, monkeypatch):  # noqa: F811
    calls = []

    @contextmanager
    def open_test_client(request, *, timeout, ssl_context):
        assert timeout == 3 and ssl_context is None
        assert request.full_url.startswith("http://hub:5000/internal/tasks/")
        calls.append(request.get_method())
        response = http.client.open(
            urllib.parse.urlsplit(request.full_url).path, method=request.get_method(),
            headers=dict(request.header_items()), data=request.data,
        )
        if response.status_code != 200:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "denied", None, None)
        yield io.BytesIO(response.data)

    monkeypatch.setattr("worker.runtime.native_graph.artifact_http.open_hub_command", open_test_client)
    transport = NativeArtifactHttpTransport(
        hub_url="http://hub:5000", bearer_token=TOKEN, worker_id="worker-1", worker_url="http://worker-1:5000",
        endpoint_prefix="", timeout_seconds=3,
    )
    content = http.ingress.content
    refs = NativeArtifactPublisher(transport=transport).publish(
        command=http.ingress.native.command, hub_task_id=http.ingress.native.task_id, project_id=None,
        artifacts=(NativeArtifactBytes("report", "report.txt", "text/plain", content,
                                       hashlib.sha256(content).hexdigest()),),
    )
    assert calls == ["GET", "POST"]
    set_result_artifact(http.ingress, refs["report"])
    assert complete(http.ingress.native).status == "completed"


def test_redirect_handler_never_builds_credential_forwarding_request():
    assert _DenyHubRedirect().redirect_request(None, None, 307, "redirect", {}, "http://foreign") is None
