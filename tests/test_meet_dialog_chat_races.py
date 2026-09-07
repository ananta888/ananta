"""Deterministic browser revocation races; no person or real chat is required."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_transport import assignment
from worker.meet_media.dialog_chat import DialogChatPump
from worker.meet_media.dialog_chat_browser import CHAT_OPERATION, DialogChatBrowser


@pytest.mark.parametrize("operation", ["poll", "ack"])
@pytest.mark.parametrize("closed", [True, False])
@pytest.mark.parametrize(
    "code",
    [
        "meet_chat_authority_changed",
        "meet_chat_authority_unavailable",
        "meet_chat_receive_denied",
        "meet_chat_closed",
        "meet_chat_endpoint_failed",
    ],
)
def test_real_browser_expression_discards_only_known_closed_queue_races(operation, closed, code):
    # Execute the exact shipped JavaScript against a deterministic endpoint.
    script = """const {expression, operation, closed, code} = JSON.parse(process.argv[1]);
      global.window = {anantaMachine:{chat:{status:()=>({open:!closed}),
        poll(){throw new Error(code)}, ack(){throw new Error(code)}}}};
      try { process.stdout.write(JSON.stringify(eval('(' + expression + ')')([operation, 1]))); }
      catch (e) { process.stdout.write(JSON.stringify({thrown:e.message})); }
    """
    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            json.dumps({"expression": CHAT_OPERATION, "operation": operation, "closed": closed, "code": code}),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    expected = {"state": "closed"} if closed and code != "meet_chat_endpoint_failed" else {"thrown": code}
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("stage", ["poll", "ack"])
def test_revocation_discards_input_without_dispatch_and_requires_fresh_hub_update(stage):
    page, hub = Mock(), Mock()
    pump = DialogChatPump(page, hub, assignment())
    pump.opened = {"generation": 1}
    pump.receipt = {"grants": [{"chatRead": True, "publisherPeerId": "synthetic-human"}]}
    pump.browser_chat = Mock()
    pump.browser_chat.poll.return_value = (
        None if stage == "poll" else {"events": [{"cursor": 1, "event": {"sender_peer_id": "synthetic-human"}}]}
    )
    pump.browser_chat.ack.return_value = False
    try:
        pump.tick()
        assert pump.opened is None and pump.pending is None
        hub.call.assert_not_called()
        before = page.evaluate.call_count
        pump.tick()
        assert page.evaluate.call_count == before  # No automatic browser-only reopen.
    finally:
        pump.close()


@pytest.mark.parametrize(
    "result", [None, True, {}, {"state": "closed", "value": {}}, {"state": "ok"}, {"state": "ok", "value": None}]
)
def test_malformed_poll_result_is_not_misreported_as_a_safe_closed_queue(result):
    page = Mock()
    page.evaluate.return_value = result
    with pytest.raises(ValueError):
        DialogChatBrowser(page).poll()


def test_poll_and_ack_positive_results_remain_explicit():
    page = Mock()
    batch = {"events": []}
    page.evaluate.side_effect = [{"state": "ok", "value": batch}, {"state": "ok", "value": None}, {"state": "closed"}]
    port = DialogChatBrowser(page)
    assert port.poll() == batch
    assert port.ack(1) is True
    assert port.ack(2) is False


@pytest.mark.parametrize(
    "mutation", [{}, {"tenant_id": "other"}, {"generation": 3}, {"policy_revision": True}, {"policy_revision": 0}]
)
def test_policy_revision_arriving_before_hub_receipt_stays_closed_but_scope_tampering_fails(mutation):
    value = assignment()
    receipt = {
        "lease": {"sessionId": "ms_" + "a" * 32, "generation": 2},
        "roomId": value["meeting"]["room_id"],
        "peerId": "b" * 16,
        "membershipEpoch": 3,
        "receiveRevision": 4,
        "grants": [{"chatRead": True}],
    }
    scope = {
        "origin": value["meeting"]["origin"],
        **{k: value[k] for k in ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")},
        "lease_id": receipt["lease"]["sessionId"],
        "generation": 2,
        "room_id": receipt["roomId"],
        "own_peer_id": receipt["peerId"],
        "membership_epoch": 3,
        "policy_revision": 5,
        "deadline_ms": 100,
    }
    page, hub = Mock(), Mock()
    page.evaluate.return_value = scope | mutation
    pump = DialogChatPump(page, hub, value)
    try:
        if mutation:
            with pytest.raises(ValueError, match="scope_changed"):
                pump.update(receipt, {"enabled": True, "revision": 1})
        else:
            pump.update(receipt, {"enabled": True, "revision": 1})
        assert pump.opened is None and pump.pending is None
        hub.call.assert_not_called()
        if not mutation:
            pump.update(receipt | {"receiveRevision": 5}, {"enabled": True, "revision": 1})
            assert pump.opened == scope
    finally:
        pump.close()
