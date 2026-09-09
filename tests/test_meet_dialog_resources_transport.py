"""Actual signed HTTP plus hostile reply transport; no human approval or task creation."""

import io
import json
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_resources import HttpDialogResources
from ananta_contracts.meet_dialog_resources import request_signature, response_signature
from tests.test_meet_dialog_resources import COUNTERS, KEY, NONCE, QUERY, value
from worker.meet_media.contract import encode
from worker.meet_media.dialog_slots import DialogSlots
from worker.meet_media.server import create_server

pytestmark = pytest.mark.timeout(15)


@pytest.fixture
def endpoint(monkeypatch):
    executor, dialog = Mock(), Mock(slots=DialogSlots(2))
    monkeypatch.setattr("worker.meet_media.dialog_resources.cgroup_snapshot", Mock(return_value=COUNTERS))
    server = create_server(("127.0.0.1", 0), KEY, executor, dialog)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, dialog
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
        executor.execute.assert_not_called()
        dialog.start.assert_not_called()


def test_actual_hub_client_reads_exact_owned_counts_with_signed_nonce_and_no_dispatch(endpoint, monkeypatch):
    server, dialog = endpoint
    pin = Mock(return_value="127.0.0.1")
    monkeypatch.setattr("agent.services.meet_dialog_resources.pin_private_container_address", pin)
    client = HttpDialogResources(f"http://synthetic-worker:{server.server_port}", KEY)
    assert dialog.slots.acquire(blocking=False)
    first = client.observe()
    assert first["slots"] == {"capacity": 2, "active": 1}
    assert first["cgroup"] == COUNTERS
    dialog.slots.release()
    second = client.observe()
    assert second["slots"]["active"] == 0 and second["nonce"] != first["nonce"]
    assert second["sampled_monotonic_us"] >= first["sampled_monotonic_us"]
    pin.assert_called_with("synthetic-worker", server.server_port)


@pytest.mark.parametrize("kind", ["unsigned", "wrong-domain", "extra", "duplicate", "oversize"])
def test_http_refuses_untrusted_or_broadened_queries_without_sampling(endpoint, monkeypatch, kind):
    server, _dialog = endpoint
    observe = Mock(side_effect=AssertionError("must not observe slots"))
    monkeypatch.setattr("worker.meet_media.dialog_resources.cgroup_snapshot", observe)
    body = QUERY
    if kind == "extra":
        body = encode(json.loads(QUERY) | {"task_id": "foreign"})
    elif kind == "duplicate":
        body = QUERY[:-1] + b',"nonce":"' + b"b" * 32 + b'"}'
    elif kind == "oversize":
        body = b"x" * 1025
    signed = request_signature(KEY, body)
    if kind == "wrong-domain":
        from ananta_contracts.meet_dialog import request_signature as dispatch_signature

        signed = dispatch_signature(KEY, body)
    elif kind == "unsigned":
        signed = ""
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/v1/dialog-resources", body, {"X-Ananta-Resources-Signature": signed}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=2)
    assert error.value.code == 409
    observe.assert_not_called()


class Reply(io.BytesIO):
    def __init__(self, raw, signed):
        super().__init__(raw)
        self.headers = {"Content-Length": str(len(raw)), "X-Ananta-Resources-Signature": signed}


@pytest.mark.parametrize("kind", ["signature", "nonce", "extra", "oversize", "transfer", "failure"])
def test_hub_rejects_stale_unbound_or_unavailable_results_without_retry(monkeypatch, kind):
    body = value()
    if kind == "nonce":
        body["nonce"] = "b" * 32
    elif kind == "extra":
        body["admitted"] = True
    raw = encode(body) if kind != "oversize" else b"x" * 1025
    signed = "wrong" if kind == "signature" else response_signature(KEY, QUERY, raw)
    reply = Reply(raw, signed)
    if kind == "transfer":
        reply.headers["Transfer-Encoding"] = "chunked"
    opener = Mock()
    opener.open.return_value = reply
    if kind == "failure":
        opener.open.side_effect = OSError("PRIVATE-MARKER")
    builder = Mock(return_value=opener)
    monkeypatch.setattr("agent.services.meet_dialog_resources.pin_private_container_address", lambda *_: "172.18.0.2")
    monkeypatch.setattr("agent.services.meet_dialog_resources.secrets.token_hex", lambda _: NONCE)
    monkeypatch.setattr("agent.services.meet_dialog_resources.urllib.request.build_opener", builder)
    with pytest.raises(MeetError, match="^meet_dialog_resources_unavailable$"):
        HttpDialogResources("http://synthetic-worker:8094", KEY).observe()
    opener.open.assert_called_once()
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://172.18.0.2:8094/v1/dialog-resources"
    assert request.data == QUERY
    assert builder.call_args.args[0].proxies == {}
    assert opener.open.call_args.kwargs["timeout"] == 2
    with pytest.raises(MeetError, match="redirect_denied"):
        builder.call_args.args[1].redirect_request(None)
