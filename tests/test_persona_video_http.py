"""Real signed HTTP/Hub evidence flow; decoder is explicitly a structural double."""

import base64
import http.client
import threading
import time
from contextlib import contextmanager

import pytest
from werkzeug.serving import WSGIRequestHandler, make_server

from agent.services.persona_video_transport import HttpPersonaVideoWorker
from ananta_contracts.persona_inspection_wire import IMAGE_WIRE, VIDEO_WIRE, parse_inspection_json
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_tasks import execute
from tests.test_persona_video_tasks import video_task as video_task
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import request_signature, result_signature
from worker.meet_media.persona_inspection_executor import PersonaInspectionExecutor
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard

pytestmark = pytest.mark.timeout(45)
KEY = b"synthetic-video-http-test-key-00001"


class SilentHandler(WSGIRequestHandler):
    def log(self, *_args, **_kwargs):
        pass


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def post(port, path, raw, *, domain=VIDEO_WIRE.domain, extra_headers=(), declared_length=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Length", str(len(raw) if declared_length is None else declared_length))
        connection.putheader("X-Ananta-Persona-Signature", request_signature(KEY, domain, raw))
        for name, value in extra_headers:
            connection.putheader(name, value)
        connection.endheaders(raw)
        response = connection.getresponse()
        body = response.read()
        assert response.getheader("Cache-Control") == "no-store"
        return response.status, body, response.getheader("X-Ananta-Persona-Result-Signature")
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["success", "revoked"])
def test_real_video_http_uses_current_hub_permission_and_durable_replay_fence(
    request, app, tmp_path, monkeypatch, mode
):
    case = request.getfixturevalue("video_task")
    app.extensions["persona_video_worker_key"] = KEY
    app.extensions["persona_video_leases"] = case.leases
    app.config["ROLE"] = "hub"
    seen = []

    class StructuralInspector:
        def __init__(self, *, require_current, deadline_monotonic):
            self.require_current = require_current
            assert time.monotonic() < deadline_monotonic <= time.monotonic() + 20

        def inspect(self, content, media_type):
            assert content == case.content and media_type == "video/mp4"
            self.require_current()
            if mode == "revoked":
                case.policy.revoke_policy(
                    case.principal, "project", case.permission.source.source_id, expected_revision=1
                )
            return case.inspected

    with serving(make_server("127.0.0.1", 0, app, threaded=True, request_handler=SilentHandler)) as hub:

        def guard_factory(assignment):
            seen.append(assignment)
            return PersonaLeaseGuard(
                f"http://127.0.0.1:{hub.server_port}/api/persona-media/v1/internal/video-lease",
                KEY,
                assignment,
                kind="video",
            )

        def executor():
            return PersonaInspectionExecutor(
                tmp_path / "video-replay.sqlite",
                guard_factory=guard_factory,
                wire=VIDEO_WIRE,
                inspector=StructuralInspector,
            )

        original = executor()
        with serving(create_inspection_server(("127.0.0.1", 0), KEY, original, wire=VIDEO_WIRE)) as worker:
            monkeypatch.setattr(
                "agent.services.persona_video_transport.pin_private_container_address", lambda *_: "127.0.0.1"
            )
            transport = HttpPersonaVideoWorker(f"http://worker.test:{worker.server_port}{VIDEO_WIRE.path}", KEY)

            def execute_and_probe(assignment, content, media_type):
                value = transport.execute(assignment, content, media_type)
                raw = encode(
                    {
                        "assignment": assignment,
                        "content": base64.b64encode(content).decode(),
                        "media_type": media_type,
                    }
                )
                # New executor uses the same SQLite file: process-local locking is insufficient.
                with pytest.raises(ValueError, match="lease_replayed"):
                    executor().execute(parse_inspection_json(raw, maximum=VIDEO_WIRE.request_limit))
                status, body, signature = post(worker.server_port, VIDEO_WIRE.path, raw)
                assert status == 409 and b"denied_or_invalid" in body
                assert signature == result_signature(KEY, VIDEO_WIRE.domain, raw, body)
                lease_raw = encode({"assignment": assignment, "nonce": "1" * 36})
                lease_path = "/api/persona-media/v1/internal/video-lease"
                assert post(hub.server_port, lease_path, lease_raw, domain=b"persona-lease-v1")[0] == 403
                assert (
                    post(
                        hub.server_port,
                        lease_path,
                        lease_raw,
                        domain=b"persona-video-lease-v1",
                        extra_headers=(("Authorization", "Bearer user-token"),),
                    )[0]
                    == 403
                )
                case.leases.require(assignment)
                return value

            case.tasks.worker.execute = execute_and_probe
            if mode == "revoked":
                with pytest.raises(ValueError, match="unavailable_or_unauthorized"):
                    execute(case)
            else:
                result = execute(case)
                case.receipts.require_completed(case.principal, case.admission, result)
                assert result.video == case.inspected
            assert seen
            with pytest.raises(PermissionError):
                guard_factory(seen[0]).require()
            run = case.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=seen[0]["run_id"])
            assert run.state == ("succeeded" if mode == "success" else "failed")
            assert run.synthetic and run.evidence_scope == "test"


@pytest.mark.parametrize("attack", ["image_domain", "image_path", "duplicate_json", "duplicate_length", "oversized"])
def test_video_worker_rejects_ambiguous_or_cross_kind_requests_before_execution(attack):
    class NeverExecute:
        def execute(self, _payload):
            pytest.fail("Invalid HTTP must not reach decoder or replay storage")

    raw, path, domain, headers = b"{}", VIDEO_WIRE.path, VIDEO_WIRE.domain, ()
    declared_length = None
    if attack == "image_domain":
        domain = IMAGE_WIRE.domain
    elif attack == "image_path":
        path = IMAGE_WIRE.path
    elif attack == "duplicate_json":
        raw = b'{"assignment":{},"assignment":{}}'
    elif attack == "duplicate_length":
        headers = (("Content-Length", "2"),)
    elif attack == "oversized":
        declared_length = VIDEO_WIRE.request_limit + 1
    with serving(create_inspection_server(("127.0.0.1", 0), KEY, NeverExecute(), wire=VIDEO_WIRE)) as worker:
        status, body, _signature = post(
            worker.server_port, path, raw, domain=domain, extra_headers=headers, declared_length=declared_length
        )
    assert status == 409 and b"denied_or_invalid" in body


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}', b"", b"[] "])
def test_shared_json_parser_rejects_duplicates_and_excess_bytes(raw):
    with pytest.raises(ValueError):
        parse_inspection_json(raw, maximum=2 if raw == b"[] " else 100)


@pytest.mark.parametrize("result", [{"allowed": 1}, {"allowed": "true"}, {"allowed": False}, {}, None])
def test_worker_requires_literal_boolean_hub_authority(monkeypatch, result):
    monkeypatch.setattr("worker.meet_media.persona_lease.signed_post", lambda *_args, **_kwargs: result)
    guard = PersonaLeaseGuard(
        "http://hub.test:5000/api/persona-media/v1/internal/video-lease",
        KEY,
        {"deadline": time.time() + 10},
        kind="video",
    )
    with pytest.raises(PermissionError, match="lease_revoked"):
        guard.require()
