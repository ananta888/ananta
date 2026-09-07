"""Real signed HTTP and Hub callbacks with test-only identities and actual descriptor validation."""

import base64
import http.client

import pytest
from werkzeug.serving import make_server

from agent.services.persona_voice_transport import create_voice_worker_transport
from ananta_contracts.persona_inspection_wire import IMAGE_WIRE, VIDEO_WIRE, VOICE_WIRE
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_http import SilentHandler, serving
from tests.test_persona_voice_tasks import execute
from tests.test_persona_voice_tasks import voice_task as voice_task
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import request_signature, result_signature
from worker.meet_media.persona_inspection_executor import PersonaInspectionExecutor
from worker.meet_media.persona_inspection_server import create_inspection_server
from worker.meet_media.persona_lease import PersonaLeaseGuard
from worker.meet_media.persona_voice_inspector import PersonaVoiceInspector

pytestmark = pytest.mark.timeout(45)
KEY = b"synthetic-voice-http-test-key-00001"


def post(port, path, raw, *, domain=VOICE_WIRE.domain, headers=(), length=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Length", str(len(raw) if length is None else length))
        connection.putheader("X-Ananta-Persona-Signature", request_signature(KEY, domain, raw))
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(raw)
        response = connection.getresponse()
        body = response.read()
        assert response.getheader("Cache-Control") == "no-store"
        return response.status, body, response.getheader("X-Ananta-Persona-Result-Signature")
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["success", "revoked"])
def test_voice_http_requires_live_hub_lease_and_retains_durable_replay_fence(request, app, tmp_path, monkeypatch, mode):
    case = request.getfixturevalue("voice_task")
    app.config["ROLE"] = "hub"
    app.extensions["persona_voice_worker_key"] = KEY
    app.extensions["persona_voice_leases"] = case.leases
    seen = []

    class Inspector:
        def __init__(self, **kwargs):
            self.inspector = PersonaVoiceInspector(**kwargs)

        def inspect(self, content, mime):
            value = self.inspector.inspect(content, mime)
            if mode == "revoked":
                case.policy.revoke_policy(
                    case.principal, "project", case.permission.source.source_id, expected_revision=1
                )
            return value

    with serving(make_server("127.0.0.1", 0, app, threaded=True, request_handler=SilentHandler)) as hub:
        callback = f"http://127.0.0.1:{hub.server_port}/api/persona-media/v1/internal/voice-lease"

        def guard(assignment):
            seen.append(assignment)
            return PersonaLeaseGuard(callback, KEY, assignment, kind="voice")

        def executor():
            return PersonaInspectionExecutor(
                tmp_path / "voice-http-replay.sqlite", wire=VOICE_WIRE, inspector=Inspector, guard_factory=guard
            )

        with serving(create_inspection_server(("127.0.0.1", 0), KEY, executor(), wire=VOICE_WIRE)) as worker:
            # Explicit loopback transport fixture; production private-DNS policy is unchanged.
            monkeypatch.setattr(
                "agent.services.persona_voice_transport.pin_private_container_address", lambda *_: "127.0.0.1"
            )
            transport = create_voice_worker_transport(f"http://worker.test:{worker.server_port}{VOICE_WIRE.path}", KEY)

            def execute_and_probe(assignment, content, mime):
                value = transport.execute(assignment, content, mime)
                payload = {"assignment": assignment, "content": base64.b64encode(content).decode(), "media_type": mime}
                with pytest.raises(ValueError, match="replayed"):
                    executor().execute(payload)
                raw = encode(payload)
                status, body, signature = post(worker.server_port, VOICE_WIRE.path, raw)
                assert status == 409 and signature == result_signature(KEY, VOICE_WIRE.domain, raw, body)
                lease = encode({"assignment": assignment, "nonce": "1" * 36})
                path = "/api/persona-media/v1/internal/voice-lease"
                for domain in (b"persona-lease-v1", b"persona-video-lease-v1"):
                    assert post(hub.server_port, path, lease, domain=domain)[0] == 403
                assert (
                    post(
                        hub.server_port,
                        path,
                        lease,
                        domain=b"persona-voice-lease-v1",
                        headers=(("Authorization", "Bearer user-token"),),
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
                assert result.voice == case.inspected
            assert seen
            with pytest.raises(PermissionError):
                guard(seen[0]).require()
            run = case.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=seen[0]["run_id"])
            assert run.state == ("succeeded" if mode == "success" else "failed")
            assert run.synthetic and run.evidence_scope == "test"


@pytest.mark.parametrize(
    "attack", ["image_domain", "video_domain", "image_path", "duplicate_json", "duplicate_length", "oversized"]
)
def test_voice_worker_rejects_ambiguous_and_cross_domain_http_before_execution(attack):
    class NeverExecute:
        def execute(self, _payload):
            pytest.fail("invalid request reached the worker")

    raw, path, domain, headers, length = b"{}", VOICE_WIRE.path, VOICE_WIRE.domain, (), None
    if attack == "image_domain":
        domain = IMAGE_WIRE.domain
    elif attack == "video_domain":
        domain = VIDEO_WIRE.domain
    elif attack == "image_path":
        path = IMAGE_WIRE.path
    elif attack == "duplicate_json":
        raw = b'{"assignment":{},"assignment":{}}'
    elif attack == "duplicate_length":
        headers = (("Content-Length", "2"),)
    else:
        length = VOICE_WIRE.request_limit + 1
    with serving(create_inspection_server(("127.0.0.1", 0), KEY, NeverExecute(), wire=VOICE_WIRE)) as worker:
        status, body, _ = post(worker.server_port, path, raw, domain=domain, headers=headers, length=length)
    assert status == 409 and b"denied_or_invalid" in body
