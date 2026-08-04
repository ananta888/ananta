import hashlib
import json
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.config import settings
from agent.services.knowledge_index_payload_authorization import (
    KnowledgeIndexPayloadAuthorizationError,
    KnowledgeIndexPayloadCapabilityAuthorizer,
)
from agent.services.workflow_worker_service_auth import (
    STRICT_WORKER_REGISTRATION_PROVENANCE,
    WORKER_REGISTRATION_KEYRING_SCHEMA,
)
from ananta_contracts.knowledge_index_payload_capability import (
    KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER,
    decode_knowledge_index_payload_capability,
    encode_knowledge_index_payload_capability,
)
from worker.retrieval.knowledge_index_execution_guard import (
    KnowledgeIndexExecutionDeadlineError,
    MonotonicKnowledgeIndexExecutionDeadline,
)
from worker.retrieval.knowledge_index_job_handler import (
    HubArtifactKnowledgeIndexPayloadLoader,
    RagHelperKnowledgeIndexExecution,
    _KnowledgeIndexPayloadNoRedirectHandler,
)

FINGERPRINT = "a" * 64
ARTIFACT_ID = "artifact-1"
WORKER_ID = "worker-1"
WORKER_URL = "http://worker-1:5000"
WORKER_TOKEN = "index-worker-service-token-0123456789abcdef"
HUB_TOKEN = "hub-service-token-0123456789abcdef"
REGISTRATION_TOKEN = "index-worker-registration-token-0123456789abcdef"
SESSION_SIGNING_KEY = "index-worker-session-key-0123456789abcdef"


@contextmanager
def _payload_response_server(
    body: bytes,
    *,
    declared_length: int | None = None,
):
    class PayloadHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Content-Length",
                str(len(body) if declared_length is None else declared_length),
            )
            self.send_header("X-Artifact-SHA256", hashlib.sha256(body).hexdigest())
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), PayloadHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _worker_record():
    return SimpleNamespace(
        name=WORKER_ID,
        url=WORKER_URL,
        token=WORKER_TOKEN,
        role="worker",
        capabilities=["retrieval", "index_write"],
        authorized_capabilities=["retrieval", "index_write"],
        registration_provenance=STRICT_WORKER_REGISTRATION_PROVENANCE,
        registration_validated=True,
        status="online",
    )


def _job():
    return {
        "job_id": f"knowledge-index-{FINGERPRINT[:32]}",
        "idempotency_fingerprint": FINGERPRINT,
        "authority_binding": {
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "source_revision_id": "revision-1",
            "source_revision_digest": "b" * 64,
            "destination_id": "destination-1",
            "destination_digest": "c" * 64,
            "source_access_grant_id": "grant-1",
            "source_access_grant_digest": "d" * 64,
            "policy_snapshot_digest": "e" * 64,
        },
        "assignment": {
            "assignment_id": "assignment-1",
            "lease_id": "lease-1",
        },
        "file_manifest": {
            "manifest_id": "manifest-1",
            "manifest_digest": "f" * 64,
        },
        "payload": {
            "payload_artifact_ref": {
                "artifact_id": ARTIFACT_ID,
                "sha256": "1" * 64,
                "size_bytes": 42,
                "media_type": (
                    "application/vnd.ananta.knowledge-index-job+json"
                ),
                "encoding": "json",
            }
        },
    }


def _manifest():
    job = _job()
    authority = job["authority_binding"]
    return {
        "schema": "ananta.source-control.enforcement-manifest.v1",
        "authority": "hub",
        "tenant_id": authority["tenant_id"],
        "project_id": authority["project_id"],
        "source_revision_id": authority["source_revision_id"],
        "source_revision_digest": authority["source_revision_digest"],
        "destination_id": authority["destination_id"],
        "destination_digest": authority["destination_digest"],
        "source_access_grant_id": authority["source_access_grant_id"],
        "source_access_grant_digest": authority[
            "source_access_grant_digest"
        ],
        "policy_digest": authority["policy_snapshot_digest"],
        "content_manifest_id": "manifest-1",
        "content_manifest_digest": "f" * 64,
        "assignment_id": "assignment-1",
        "lease_id": "lease-1",
        "grant_expires_at_epoch_ms": 20_000,
        "signature": "signed",
    }


class _Verifier:
    def verify_manifest(self, manifest):
        return manifest.get("signature") == "signed"


class _Bindings:
    def validate_delegated_payload_access(
        self,
        *,
        assignment_id,
        lease_id,
        authenticated_worker_id,
    ):
        assert assignment_id == "assignment-1"
        assert lease_id == "lease-1"
        assert authenticated_worker_id == WORKER_ID
        return SimpleNamespace(
            job=SimpleNamespace(to_wire=_job)
        )


class _Agents:
    def get_by_url(self, worker_url):
        if worker_url != WORKER_URL:
            return None
        return _worker_record()

    def get_all(self):
        return [_worker_record()]


def _configure_registered_worker_auth(
    app: Flask,
    *,
    tmp_path,
    monkeypatch,
    artifact_repo=None,
    artifact_version_repo=None,
) -> None:
    keyring = tmp_path / "index-worker-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    WORKER_ID: {
                        "worker_url": WORKER_URL,
                        "registration_token": REGISTRATION_TOKEN,
                        "service_token_sha256": _sha256(WORKER_TOKEN),
                        "session_signing_key_sha256": _sha256(
                            SESSION_SIGNING_KEY
                        ),
                        "allowed_capabilities": [
                            "index_write",
                            "retrieval",
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    keyring.chmod(0o440)
    app.secret_key = "test-session-secret-0123456789abcdef"
    app.config.update(
        TESTING=True,
        AGENT_TOKEN=HUB_TOKEN,
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(keyring),
    )
    app.extensions["repository_registry"] = SimpleNamespace(
        agent_repo=_Agents(),
        artifact_repo=artifact_repo,
        artifact_version_repo=artifact_version_repo,
    )
    monkeypatch.setattr("agent.auth.log_audit", lambda *_args, **_kwargs: None)


def _worker_headers(*, capability: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {WORKER_TOKEN}",
        "X-Ananta-Worker-ID": WORKER_ID,
        "X-Ananta-Worker-URL": WORKER_URL,
    }
    if capability:
        headers[KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER] = (
            encode_knowledge_index_payload_capability(_manifest())
        )
    return headers


def _authorizer():
    return KnowledgeIndexPayloadCapabilityAuthorizer(
        execution_binding_service=_Bindings(),
        manifest_verifier=_Verifier(),
        agent_repository=_Agents(),
        clock_ms=lambda: 10_000,
    )


def test_capability_codec_and_live_assignment_authorization():
    manifest = _manifest()
    encoded = encode_knowledge_index_payload_capability(manifest)

    assert decode_knowledge_index_payload_capability(encoded) == manifest
    assert _authorizer().authorize(
        artifact_id=ARTIFACT_ID,
        artifact_sha256="1" * 64,
        artifact_size_bytes=42,
        artifact_media_type=(
            "application/vnd.ananta.knowledge-index-job+json"
        ),
        manifest=manifest,
        worker_id=WORKER_ID,
        worker_url=WORKER_URL,
    ) == f"knowledge-index-{FINGERPRINT[:32]}"


def test_capability_rejects_artifact_or_assignment_mismatch():
    with pytest.raises(KnowledgeIndexPayloadAuthorizationError):
        _authorizer().authorize(
            artifact_id="artifact-other",
            artifact_sha256="1" * 64,
            artifact_size_bytes=42,
            artifact_media_type=(
                "application/vnd.ananta.knowledge-index-job+json"
            ),
            manifest=_manifest(),
            worker_id=WORKER_ID,
            worker_url=WORKER_URL,
        )

    changed = _manifest()
    changed["assignment_id"] = "assignment-other"
    with pytest.raises(KnowledgeIndexPayloadAuthorizationError):
        _authorizer().authorize(
            artifact_id=ARTIFACT_ID,
            artifact_sha256="1" * 64,
            artifact_size_bytes=42,
            artifact_media_type=(
                "application/vnd.ananta.knowledge-index-job+json"
            ),
            manifest=changed,
            worker_id=WORKER_ID,
            worker_url=WORKER_URL,
        )


def test_internal_payload_route_rejects_registered_worker_without_capability(
    monkeypatch,
    tmp_path,
):
    from agent.routes.artifacts import (
        get_knowledge_index_payload_artifact,
    )

    app = Flask(__name__)
    _configure_registered_worker_auth(
        app,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    with app.test_request_context(headers=_worker_headers()):
        response, status_code = get_knowledge_index_payload_artifact(
            ARTIFACT_ID
        )

    assert status_code == 401
    assert response.get_json()["data"]["reason_code"] == (
        "knowledge_index_payload_capability_required"
    )


def test_internal_payload_route_rejects_capability_without_registered_worker(
    monkeypatch,
    tmp_path,
):
    from agent.routes.artifacts import (
        get_knowledge_index_payload_artifact,
    )

    app = Flask(__name__)
    _configure_registered_worker_auth(
        app,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    headers = _worker_headers(capability=True)
    headers["Authorization"] = f"Bearer {HUB_TOKEN}"
    with app.test_request_context(headers=headers):
        response, status_code = get_knowledge_index_payload_artifact(
            ARTIFACT_ID
        )

    assert status_code == 401
    assert response.get_json()["data"]["reason_code"] == (
        "workflow_worker_service_token_invalid"
    )


def test_internal_payload_route_requires_worker_auth_and_capability(
    monkeypatch,
    tmp_path,
):
    from agent.routes.artifacts import (
        get_knowledge_index_payload_artifact,
    )

    content = b"x" * 42
    digest = hashlib.sha256(content).hexdigest()
    storage_path = tmp_path / "payload.json"
    storage_path.write_bytes(content)
    artifact = SimpleNamespace(
        latest_version_id="version-1",
        artifact_metadata={
            "system_artifact_kind": "knowledge_index_job_payload"
        },
    )
    version = SimpleNamespace(
        sha256=digest,
        size_bytes=len(content),
        media_type="application/vnd.ananta.knowledge-index-job+json",
        storage_path=str(storage_path),
        original_filename="payload.json",
    )

    class ByIdRepository:
        def __init__(self, expected_id, value):
            self.expected_id = expected_id
            self.value = value

        def get_by_id(self, item_id):
            return self.value if item_id == self.expected_id else None

    captured = {}

    class Authorizer:
        def authorize(self, **kwargs):
            captured.update(kwargs)

    app = Flask(__name__)
    _configure_registered_worker_auth(
        app,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        artifact_repo=ByIdRepository(ARTIFACT_ID, artifact),
        artifact_version_repo=ByIdRepository("version-1", version),
    )
    app.extensions[
        "knowledge_index_payload_capability_authorizer"
    ] = Authorizer()
    replacement = b"y" * len(content)

    def swap_path_before_send(source, **kwargs):
        storage_path.write_bytes(replacement)
        if hasattr(source, "read"):
            served = source.read()
        else:
            with open(source, "rb") as path_handle:
                served = path_handle.read()
        return app.response_class(
            served,
            mimetype=kwargs.get("mimetype"),
        )

    monkeypatch.setattr(
        "agent.routes.artifacts.send_file",
        swap_path_before_send,
    )
    with app.test_request_context(
        headers=_worker_headers(capability=True)
    ):
        response = get_knowledge_index_payload_artifact(ARTIFACT_ID)

    assert response.status_code == 200
    assert response.get_data() == content
    assert storage_path.read_bytes() == replacement
    assert response.headers["X-Artifact-SHA256"] == digest
    assert response.headers["X-Artifact-Size"] == str(len(content))
    assert captured["worker_id"] == WORKER_ID
    assert captured["worker_url"] == WORKER_URL
    response.close()


def test_governed_payload_loader_always_revalidates_through_hub(
    monkeypatch,
):
    captured = {}

    def fail_local(*_args, **_kwargs):
        pytest.fail("governed payload access must not read local storage")

    def load_from_hub(
        artifact_id,
        *,
        expected_size,
        expected_sha256,
        source_access_manifest=None,
    ):
        captured.update(
            {
                "artifact_id": artifact_id,
                "expected_size": expected_size,
                "expected_sha256": expected_sha256,
                "source_access_manifest": source_access_manifest,
            }
        )
        return b"x" * 42

    monkeypatch.setattr(
        HubArtifactKnowledgeIndexPayloadLoader,
        "_load_local",
        staticmethod(fail_local),
    )
    monkeypatch.setattr(
        HubArtifactKnowledgeIndexPayloadLoader,
        "_load_from_hub",
        staticmethod(load_from_hub),
    )
    reference = _job()["payload"]["payload_artifact_ref"]

    content = HubArtifactKnowledgeIndexPayloadLoader().load_authorized(
        reference,
        source_access_manifest=_manifest(),
    )

    assert content == b"x" * 42
    assert captured["artifact_id"] == ARTIFACT_ID
    assert captured["source_access_manifest"] == _manifest()


def test_governed_payload_loader_rejects_redirect_without_forwarding_capability(
    monkeypatch,
) -> None:
    class RedirectResponse:
        status = 307
        headers = {"Location": "http://metadata.internal/credentials"}

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Opener:
        def __init__(self, response) -> None:
            self.response = response
            self.requests = []

        def open(self, request, *, timeout):
            assert timeout == 60
            self.requests.append(request)
            return self.response

    response = RedirectResponse()
    opener = Opener(response)

    def build_opener(handler):
        assert isinstance(handler, _KnowledgeIndexPayloadNoRedirectHandler)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(settings, "agent_name", "")
    monkeypatch.setattr(settings, "agent_url", "")
    monkeypatch.setattr(
        "agent.auth.resolve_configured_agent_token",
        lambda _config: WORKER_TOKEN,
    )

    with pytest.raises(
        ValueError,
        match="knowledge_index_payload_redirect_forbidden",
    ):
        HubArtifactKnowledgeIndexPayloadLoader._load_from_hub(
            ARTIFACT_ID,
            expected_size=42,
            expected_sha256="f" * 64,
            source_access_manifest=_manifest(),
        )

    assert len(opener.requests) == 1
    assert opener.requests[0].full_url.startswith("http://hub:5000/")
    assert opener.requests[0].get_header("Authorization") == (
        f"Bearer {WORKER_TOKEN}"
    )
    assert response.closed is True


def test_governed_payload_read_enforces_absolute_slow_drip_deadline() -> None:
    now = [100.0]
    socket_timeouts = []

    class Socket:
        def settimeout(self, timeout):
            socket_timeouts.append(timeout)

    class Response:
        fp = SimpleNamespace(
            raw=SimpleNamespace(_sock=Socket()),
        )

        def read1(self, _size):
            now[0] += 0.6
            return b"x"

    deadline = MonotonicKnowledgeIndexExecutionDeadline(
        expires_at_monotonic=101.0,
        monotonic_clock=lambda: now[0],
    )

    assert HubArtifactKnowledgeIndexPayloadLoader._read_chunk(
        Response(),
        1,
        execution_deadline=deadline,
    ) == b"x"
    with pytest.raises(
        KnowledgeIndexExecutionDeadlineError,
        match="knowledge_index_worker_execution_deadline_exceeded",
    ):
        HubArtifactKnowledgeIndexPayloadLoader._read_chunk(
            Response(),
            1,
            execution_deadline=deadline,
        )

    assert socket_timeouts == pytest.approx([1.0, 0.4])


def test_governed_payload_accepts_complete_connection_close_response(
    monkeypatch,
) -> None:
    content = b"x" * 70_000
    deadline = MonotonicKnowledgeIndexExecutionDeadline(
        expires_at_monotonic=105.0,
        monotonic_clock=lambda: 100.0,
    )

    with _payload_response_server(content) as hub_url:
        monkeypatch.setattr(settings, "hub_url", hub_url)
        monkeypatch.setattr(settings, "agent_name", "")
        monkeypatch.setattr(settings, "agent_url", "")
        monkeypatch.setattr(
            "agent.auth.resolve_configured_agent_token",
            lambda _config: WORKER_TOKEN,
        )

        loaded = HubArtifactKnowledgeIndexPayloadLoader._load_from_hub(
            ARTIFACT_ID,
            expected_size=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            source_access_manifest=_manifest(),
            execution_deadline=deadline,
        )

    assert loaded == content


def test_governed_payload_rejects_connection_close_before_declared_length(
    monkeypatch,
) -> None:
    content = b"x" * 70_000
    declared_length = len(content) + 17
    deadline = MonotonicKnowledgeIndexExecutionDeadline(
        expires_at_monotonic=105.0,
        monotonic_clock=lambda: 100.0,
    )

    with _payload_response_server(
        content,
        declared_length=declared_length,
    ) as hub_url:
        monkeypatch.setattr(settings, "hub_url", hub_url)
        monkeypatch.setattr(settings, "agent_name", "")
        monkeypatch.setattr(settings, "agent_url", "")
        monkeypatch.setattr(
            "agent.auth.resolve_configured_agent_token",
            lambda _config: WORKER_TOKEN,
        )

        with pytest.raises(
            ValueError,
            match="knowledge_index_payload_artifact_size_mismatch",
        ):
            HubArtifactKnowledgeIndexPayloadLoader._load_from_hub(
                ARTIFACT_ID,
                expected_size=declared_length,
                expected_sha256=hashlib.sha256(content).hexdigest(),
                source_access_manifest=_manifest(),
                execution_deadline=deadline,
            )


def test_governed_payload_loader_requires_worker_service_token(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(
        "agent.auth.resolve_configured_agent_token",
        lambda _config: None,
    )
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_args: pytest.fail("missing token must fail before transport"),
    )

    with pytest.raises(
        ValueError,
        match="knowledge_index_payload_worker_service_token_required",
    ):
        HubArtifactKnowledgeIndexPayloadLoader._load_from_hub(
            ARTIFACT_ID,
            expected_size=42,
            expected_sha256="f" * 64,
            source_access_manifest=_manifest(),
        )


def test_legacy_payload_loader_keeps_local_compatibility(monkeypatch):
    monkeypatch.setattr(
        HubArtifactKnowledgeIndexPayloadLoader,
        "_load_local",
        staticmethod(
            lambda _artifact_id, *, expected_size: b"x" * expected_size
        ),
    )
    monkeypatch.setattr(
        HubArtifactKnowledgeIndexPayloadLoader,
        "_load_from_hub",
        staticmethod(
            lambda *_args, **_kwargs: pytest.fail(
                "legacy local payload should not call Hub"
            )
        ),
    )

    assert HubArtifactKnowledgeIndexPayloadLoader().load(
        _job()["payload"]["payload_artifact_ref"]
    ) == b"x" * 42


def test_v2_payload_resolution_never_falls_back_to_legacy_loader():
    class LegacyOnlyLoader:
        def load(self, _reference):
            pytest.fail("governed v2 payload used legacy loader")

    governed_job = {
        **_job(),
        "schema": "ananta.knowledge_index_execution_job.v2",
        "source_access_enforcement_manifest": _manifest(),
    }
    execution = RagHelperKnowledgeIndexExecution(
        object(),
        payload_loader=LegacyOnlyLoader(),
    )

    with pytest.raises(
        ValueError,
        match="authorized_payload_loader_required",
    ):
        execution._resolve_payload(governed_job)

    class AuthorizedLoader:
        def load_authorized(self, _reference, **_kwargs):
            pytest.fail("missing capability reached loader")

    without_capability = dict(governed_job)
    without_capability.pop("source_access_enforcement_manifest")
    with pytest.raises(ValueError, match="payload_capability_required"):
        RagHelperKnowledgeIndexExecution(
            object(),
            payload_loader=AuthorizedLoader(),
        )._resolve_payload(without_capability)
