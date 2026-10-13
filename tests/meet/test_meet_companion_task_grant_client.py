"""Worker verification against actual Hub route and synthetic transport faults."""

import io
import json
import threading
import time
from contextlib import contextmanager

import pytest
from flask import redirect, request
from werkzeug.serving import WSGIRequestHandler, make_server

from ananta_contracts.meet_companion_task_grant import SIGNATURE_HEADER, response_signature
from tests.meet.test_meet_companion_task_grant_routes import hub as hub
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_dialog_transport import assignment as dialog_assignment
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.contract import encode

KEY = b"synthetic-task-companion-worker-key"
URL = "http://127.0.0.1/api/meet/v1/internal/companion/task-grant"


def assignment():
    return dialog_assignment() | {"companion_execution": True}


class HubTransport:
    def __init__(self, app, transform=None):
        self.app, self.transform, self.requests = app, transform, []

    @contextmanager
    def open(self, request, timeout):
        assert 0 < timeout <= 2.5
        self.requests.append(request.data)
        result = self.app.test_client().post("/api/meet/v1/internal/companion/task-grant", data=request.data,
                                            headers=dict(request.header_items()))
        if result.status_code != 200:
            raise RuntimeError("synthetic HTTP denial")
        body, signature = result.data, result.headers[SIGNATURE_HEADER]
        if self.transform:
            body, signature = self.transform(request.data, body, signature)
        response = io.BytesIO(body)
        response.headers = {SIGNATURE_HEADER: signature}
        with response:
            yield response


def client(hub, transform=None):
    app, f, _payload, _key = hub
    value = assignment() | {"session_id": "hub-session", "deadline": f.context["deadline"]}
    transport = HubTransport(app, transform)
    return AssignedCompanionGrantClient(value, URL, KEY, opener=transport), transport


def test_worker_consumes_current_exact_signed_hub_task_and_denies_cancellation(hub):
    value, transport = client(hub)
    one, two = value.read(), value.read()
    assert one["task_id"] == "task" and two["controls"] == hub[1].context["controls"]
    assert json.loads(transport.requests[0])["nonce"] != json.loads(transport.requests[1])["nonce"]
    hub[1].task.status = "cancelled"
    with pytest.raises(ValueError, match="unavailable_or_invalid"):
        value.read()


@pytest.mark.parametrize("change", ["task_id", "lease_id", "runtime_id", "session_id", "project_id",
                                    "origin", "room_id", "capabilities", "deadline", "controls", "extra"])
def test_even_signed_response_cannot_change_assignment_or_controls_shape(hub, change):
    def transform(raw, body, _signature):
        response = json.loads(body)
        if change == "capabilities":
            response[change].append("avatar.publish")
        elif change == "deadline":
            response[change] += 1
        elif change == "controls":
            response[change]["screen"]["enabled"] = True
        elif change == "extra":
            response[change] = True
        else:
            response[change] = "replacement"
        body = encode(response)
        return body, response_signature(KEY, raw, body)

    value, _transport = client(hub, transform)
    with pytest.raises(ValueError, match="unavailable_or_invalid"):
        value.read()


def test_signed_old_response_does_not_satisfy_new_nonce(hub):
    previous = []

    def replay(_raw, body, signature):
        if not previous:
            previous.append((body, signature))
        return previous[0]

    value, _transport = client(hub, replay)
    value.read()
    with pytest.raises(ValueError, match="unavailable_or_invalid"):
        value.read()


@pytest.mark.parametrize("signature", ["", "f" * 64, "not-a-signature"])
def test_missing_or_invalid_signature_has_no_cached_authority_fallback(hub, signature):
    value, _transport = client(hub, lambda _raw, body, _signature: (body, signature))
    with pytest.raises(ValueError, match="unavailable_or_invalid"):
        value.read()


def test_signed_oversize_body_fails_bounded(hub):
    def oversized(raw, _body, _signature):
        body = b'x' * 8193
        return body, response_signature(KEY, raw, body)
    value, _transport = client(hub, oversized)
    with pytest.raises(ValueError, match="unavailable_or_invalid"):
        value.read()


@pytest.mark.parametrize("url", ["https://user:secret@example.test/api/meet/v1/internal/companion/task-grant",
                                 URL + "?fallback=legacy", URL + "#fragment", URL.replace("task-grant", "grant")])
def test_endpoint_is_operator_fixed_and_cannot_select_legacy_or_credentials(url):
    with pytest.raises(ValueError, match="endpoint_invalid"):
        AssignedCompanionGrantClient(assignment(), url, KEY)


def test_worker_cannot_activate_companion_using_unnegotiated_dialog_assignment():
    with pytest.raises(ValueError, match="profile_invalid"):
        AssignedCompanionGrantClient(dialog_assignment(), URL, KEY)


def test_delayed_transport_cannot_return_fresh_controls(hub):
    now = [time.monotonic()]
    value, transport = client(hub)
    value.monotonic = lambda: now[0]
    transport.transform = lambda _raw, body, signature: (now.__setitem__(0, now[0] + 3) or body, signature)
    with pytest.raises(ValueError, match="unavailable_or_invalid"):
        value.read()


def test_real_http_transport_ignores_environment_proxy_and_denies_redirect(hub, monkeypatch):
    app, f, _payload, _key = hub
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    redirecting, captures = [False], []

    @app.before_request
    def redirect_probe():
        if request.path == "/sink":
            captures.append(1)
            return "forbidden delegated target"
        if redirecting[0]:
            return redirect("/sink", code=307)

    class Quiet(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    server = make_server("127.0.0.1", 0, app, request_handler=Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        value = assignment() | {"session_id": "hub-session", "deadline": f.context["deadline"]}
        endpoint = URL.replace("127.0.0.1", "127.0.0.1:" + str(server.server_port))
        worker = AssignedCompanionGrantClient(value, endpoint, KEY)
        assert worker.read()["controls"] == f.context["controls"]
        redirecting[0] = True
        with pytest.raises(ValueError, match="unavailable_or_invalid"):
            worker.read()
        assert captures == []
        redirecting[0] = False
        f.task.status = "cancelled"
        with pytest.raises(ValueError, match="unavailable_or_invalid"):
            worker.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
