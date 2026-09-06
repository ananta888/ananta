"""Synthetic local HTTP and task lifecycle checks, not deployment evidence."""

import json
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_dialog import parse, request_signature, response_signature, validate_assignment, validate_callback
from worker.meet_media.contract import encode
from worker.meet_media.server import create_server
from worker.meet_media.dialog_runtime import chat_scope_matches
from agent.services.meet_dialog_service import MeetDialogService
from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.test_meet_dialog_authority import fixture


def assignment():
    return {"schema": "ananta.meet-dialog-assignment.v1", "task_id": "task", "lease_id": "dispatch", "runtime_id": "runtime",
        "session_id": "session", "tenant_id": "tenant", "project_id": "project", "deadline": int(time.time()) + 600,
        "capabilities": ["chat.read", "chat.send"], "audio_mode": "off",
        "meeting": {"origin": "https://meet.example.test", "room_id": "room-" + "a" * 18, "grant": "signed.jwt.grant"}}


def test_closed_contract_and_direction_request_binding():
    value = assignment(); now = time.time()
    assert validate_assignment(parse(encode(value)), now) == value
    for patch in ({"unknown": 1}, {"deadline": True}, {"capabilities": ["chat.read", "chat.read"]},
                  {"capabilities": ["record"]}, {"meeting": value["meeting"] | {"origin": "https://meet.example.test/other"}}):
        with pytest.raises(ValueError): validate_assignment(value | patch, now)
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff', b'[]' * 10000):
        with pytest.raises(ValueError): parse(raw)
    key = b"test" * 8; body = encode(value); result = b'{}'
    assert request_signature(key, body) != response_signature(key, body, result)
    assert response_signature(key, body, result) != response_signature(key, body + b' ', result)
    callback = {"schema": "ananta.meet-dialog-callback.v1", "action": "exchange", "task_id": "task", "lease_id": "lease",
                "runtime_id": "runtime", "nonce": "a" * 32, "sent_at": int(now), "meet_session_id": "ms_" + "a" * 32}
    assert validate_callback(callback, now) is callback
    for patch in ({"action": "start"}, {"sent_at": int(now) - 11}, {"sent_at": True}, {"allowed": True}, {"nonce": "bad"}):
        with pytest.raises(ValueError): validate_callback(callback | patch, now)


def test_actual_worker_http_dialog_is_opt_in_and_uses_own_signature_domain():
    key = b"test" * 8; value = assignment(); body = encode(value)
    accepted = {"schema": "ananta.meet-dialog-accepted.v1", **{k: value[k] for k in ("task_id", "lease_id", "runtime_id")}, "status": "accepted"}
    dialog = Mock(); dialog.start.return_value = accepted
    server = create_server(("127.0.0.1", 0), key, Mock(), dialog)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/dialogs"
        headers = {"Content-Type": "application/json", "X-Ananta-Dialog-Signature": request_signature(key, body)}
        with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=2) as response:
            raw = response.read()
            assert response.headers["X-Ananta-Dialog-Signature"] == response_signature(key, body, raw)
            assert json.loads(raw) == accepted
        dialog.start.assert_called_once_with(value)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url, body, {"X-Ananta-Task-Signature": request_signature(key, body)}), timeout=2)
        assert error.value.code == 409
        dialog.start.assert_called_once()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_hub_failure_cleanup_and_expired_session_stop_do_not_need_new_authority():
    f = fixture(); worker = Mock(); worker.start_dialog.side_effect = MeetError("meet_worker_unavailable", 503)
    issuer = Mock(); issuer.issue_dialog.return_value = assignment()["meeting"]
    service = MeetDialogService(f.authority, f.tasks, Mock(), issuer, worker, Mock(), Mock(), Mock(), clock=lambda: f.now)
    principal = HubSourcePrincipal("owner", "tenant", "project", frozenset({"user"}))
    # Authority rejects freshly generated IDs in this fixed fixture: cleanup still occurs.
    with pytest.raises(MeetError): service.start(principal, "project", {"capabilities": ["chat.read", "chat.send"], "duration_seconds": 300, "chat_mode": "mention"})
    f.tasks.finish_bound.assert_called_once()
    f.context["deadline"] = f.now - 1
    service.inspect(principal, "project", "task", stop=True)
    f.tasks.finish_bound.assert_called_with("task", "dispatch", "runtime", "cancelled")
    service.finish({"task_id": "task", "lease_id": "dispatch", "runtime_id": "runtime", "status": "failed", "nonce": "a" * 32})
    f.tasks.finish_bound.assert_called_with("task", "dispatch", "runtime", "failed")


def test_worker_scope_mapping_keeps_distinct_leases_and_generations():
    value = assignment()
    receipt = {"lease": {"sessionId": "ms_" + "a" * 32, "generation": 2}, "roomId": value["meeting"]["room_id"],
               "peerId": "b" * 16, "membershipEpoch": 3, "receiveRevision": 4}
    scope = {"origin": value["meeting"]["origin"], **{k: value[k] for k in ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")},
             "lease_id": receipt["lease"]["sessionId"], "generation": 2, "room_id": receipt["roomId"], "own_peer_id": receipt["peerId"],
             "membership_epoch": 3, "policy_revision": 4, "deadline_ms": 100}
    assert chat_scope_matches(scope, receipt, value)
    for patch in ({"lease_id": value["lease_id"]}, {"generation": 1}, {"policy_revision": 3}, {"extra": 1},
                  {"deadline_ms": True}, {"deadline_ms": "100"}, {"deadline_ms": 0}, {"deadline_ms": 2**53}):
        assert not chat_scope_matches(scope | patch, receipt, value)
