"""Strict HTTP auth with real Native SQL dispatch, Hub storage and registration."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.routes.tasks.native_artifact_ingress import MAX_HTTP_BYTES, native_artifact_ingress_bp
from agent.services.native_artifact_runtime import NativeArtifactRuntime, get_native_artifact_runtime
from agent.services.workflow_worker_service_auth import WORKER_REGISTRATION_KEYRING_SCHEMA
from ananta_contracts.native_artifact_transfer import MAX_ARTIFACT_BYTES, NativeArtifactBytes
from tests.bpmn.test_native_artifact_admission import ingress  # noqa: F401
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import (
    admission,  # noqa: F401
    complete,
)
from worker.runtime.native_graph.artifact_publication import NativeArtifactPublisher

TOKEN = "synthetic-worker-token-" + "x" * 32
HEADERS = {
    "Authorization": f"Bearer {TOKEN}", "X-Ananta-Worker-ID": "worker-1",
    "X-Ananta-Worker-URL": "http://worker-1:5000",
}


@pytest.fixture
def http(ingress, monkeypatch, tmp_path):  # noqa: F811
    app = Flask(__name__)
    app.secret_key = "synthetic-session-secret-" + "s" * 32
    capabilities = ["workflow.adapter.native", "coding.agent.pi", "text_generation"]
    keyring = tmp_path / "worker-keyring.json"
    keyring.write_text(json.dumps({"schema": WORKER_REGISTRATION_KEYRING_SCHEMA, "workers": {"worker-1": {
        "worker_url": HEADERS["X-Ananta-Worker-URL"], "registration_token": "r" * 48,
        "service_token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
        "session_signing_key_sha256": hashlib.sha256(b"k" * 48).hexdigest(),
        "allowed_capabilities": capabilities,
    }}}))
    keyring.chmod(0o440)
    app.config.update(
        TESTING=True, AGENT_TOKEN="synthetic-hub-token-" + "h" * 32,
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(keyring),
    )
    worker = SimpleNamespace(
        name="worker-1", url=HEADERS["X-Ananta-Worker-URL"], token=TOKEN, role="worker",
        capabilities=capabilities, authorized_capabilities=capabilities, registration_validated=True,
        registration_provenance="strict_registration_keyring_v1", status="online",
    )
    app.extensions["repository_registry"] = SimpleNamespace(agent_repo=SimpleNamespace(get_all=lambda: [worker]))
    runtime = NativeArtifactRuntime(
        engine=ingress.native.engine, key_ring=lambda: ingress.native.keys, storage=ingress.store,
        clock=lambda: ingress.native.clock.now,
    )
    monkeypatch.setattr("agent.routes.tasks.native_artifact_ingress.get_native_artifact_runtime", lambda: runtime)
    monkeypatch.setattr("agent.routes.tasks.native_artifact_ingress.log_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.auth.log_audit", lambda *args, **kwargs: None)
    app.register_blueprint(native_artifact_ingress_bp)
    return SimpleNamespace(app=app, client=app.test_client(), ingress=ingress,
                           url=f"/internal/tasks/{ingress.native.task_id}/native-artifacts")


@pytest.mark.parametrize("prefix", ["", "/api"])
def test_authenticated_binding_and_real_byte_publication(http, prefix):
    http.url = prefix + http.url
    binding = http.client.get(http.url, headers=HEADERS)
    assert binding.status_code == 200, binding.get_json()
    assert binding.get_json()["data"]["binding"] == http.ingress.transfer["binding"]
    response = http.client.post(http.url, headers=HEADERS, json=http.ingress.transfer)
    assert response.status_code == 200, response.get_json()
    receipts = response.get_json()["data"]["artifacts"]
    assert len(receipts) == 1 and receipts[0]["artifact_ref"].startswith("artifact://native-artifact-")
    assert http.client.post(http.url, headers=HEADERS, json=http.ingress.transfer).get_json() == response.get_json()


@pytest.mark.parametrize("prefix", ["", "/api"])
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("headers", [
    {}, {**HEADERS, "Authorization": "Bearer invalid"},
    {**HEADERS, "X-Ananta-Worker-ID": "foreign-worker"},
    {**HEADERS, "X-Ananta-Worker-URL": "http://foreign-worker:5000"},
])
def test_unauthorized_call_has_no_storage_side_effect(http, method, headers, prefix):
    http.url = prefix + http.url
    response = getattr(http.client, method)(http.url, headers=headers, json=http.ingress.transfer)
    assert response.status_code in {401, 403}, response.get_json()
    assert list(http.ingress.store.base_dir.iterdir()) == []


def test_hub_shared_token_does_not_substitute_for_registered_worker(http):
    response = http.client.get(
        http.url, headers={**HEADERS, "Authorization": f"Bearer {http.app.config['AGENT_TOKEN']}"},
    )
    assert response.status_code in {401, 403}


@pytest.mark.parametrize("payload", [None, [], {"forged": True}])
def test_malformed_publication_cannot_store_any_bytes(http, payload):
    response = http.client.post(http.url, headers=HEADERS, json=payload)
    assert response.status_code in {400, 409, 413}
    assert list(http.ingress.store.base_dir.iterdir()) == []


def test_expired_native_lease_denies_binding_and_transfer(http):
    http.ingress.native.clock.now += 1000
    assert http.client.get(http.url, headers=HEADERS).status_code == 409
    assert http.client.post(http.url, headers=HEADERS, json=http.ingress.transfer).status_code == 409
    assert list(http.ingress.store.base_dir.iterdir()) == []


def test_revoked_signature_returns_bounded_denial(http):
    http.ingress.native.keys.rotate(key_id="key-1", key=b"z" * 32)
    response = http.client.post(http.url, headers=HEADERS, json=http.ingress.transfer)
    assert response.status_code == 409
    assert "signature" in response.get_json()["data"]["reason_code"]
    assert list(http.ingress.store.base_dir.iterdir()) == []


def test_oversize_request_is_denied_before_body_read(http):
    response = http.client.post(
        http.url, headers=HEADERS, data=b"{}", content_type="application/json",
        environ_overrides={"CONTENT_LENGTH": str(MAX_HTTP_BYTES + 1)},
    )
    assert response.status_code == 413
    assert list(http.ingress.store.base_dir.iterdir()) == []


def test_production_composition_requires_hub_role(monkeypatch):
    monkeypatch.setattr("agent.config.settings.role", "worker")
    with pytest.raises(ValueError, match="native_artifact_hub_required"):
        get_native_artifact_runtime()


class FlaskArtifactTransport:
    def __init__(self, http, *, tamper=None):
        self.http, self.tamper, self.calls = http, tamper, []

    def request(self, *, method, path, payload=None):
        self.calls.append(method)
        response = self.http.client.open(path, method=method, headers=HEADERS, json=payload)
        if response.status_code != 200:
            raise ValueError("hub_transfer_denied")
        value = copy.deepcopy(response.get_json()["data"])
        if self.tamper is not None:
            self.tamper(method, value)
        return value


def publish(http, transport):
    content = http.ingress.content
    return NativeArtifactPublisher(transport=transport).publish(
        command=http.ingress.native.command, hub_task_id=http.ingress.native.task_id, project_id=None,
        artifacts=(NativeArtifactBytes("report", "report.txt", "text/plain", content,
                                       hashlib.sha256(content).hexdigest()),),
    )


def test_worker_publication_through_real_hub_auth_and_completion(http):
    references = publish(http, FlaskArtifactTransport(http))
    from tests.bpmn.test_native_artifact_admission import set_result_artifact

    set_result_artifact(http.ingress, references["report"])
    assert complete(http.ingress.native).status == "completed"


@pytest.mark.parametrize("field,value", [
    ("filename", "foreign.txt"), ("sha256", "a" * 64), ("size_bytes", True),
    ("tenant_id", "foreign"), ("project_id", "foreign"), ("classification", "production"),
    ("artifact_id", "SRC_forged"), ("artifact_version_id", "worker-local-version"),
    ("artifact_ref", "artifact://worker-local-artifact"),
])
def test_worker_rejects_modified_hub_receipt(http, field, value):
    def tamper(method, response):
        if method == "POST":
            response["artifacts"][0][field] = value

    with pytest.raises(ValueError, match="native_artifact_receipt"):
        publish(http, FlaskArtifactTransport(http, tamper=tamper))
    assert http.ingress.native.repository.get_by_id(http.ingress.native.task_id).status == "in_progress"


def test_worker_rejects_foreign_binding_before_posting_bytes(http):
    def tamper(method, response):
        if method == "GET":
            response["binding"]["attempt_id"] = "foreign"

    transport = FlaskArtifactTransport(http, tamper=tamper)
    with pytest.raises(ValueError, match="binding_response_mismatch"):
        publish(http, transport)
    assert transport.calls == ["GET"]
    assert list(http.ingress.store.base_dir.iterdir()) == []


def test_worker_rejects_oversized_bytes_before_any_transport(http):
    transport = FlaskArtifactTransport(http)
    with pytest.raises(ValueError, match="publication_size_invalid"):
        NativeArtifactPublisher(transport=transport).publish(
            command=http.ingress.native.command, hub_task_id=http.ingress.native.task_id, project_id=None,
            artifacts=(NativeArtifactBytes("report", "report.txt", "text/plain", b"x" * (MAX_ARTIFACT_BYTES + 1),
                                           "a" * 64),),
        )
    assert transport.calls == []
