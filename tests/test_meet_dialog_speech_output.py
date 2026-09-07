"""Deterministic current-control and publication races; no live media claim."""

import copy
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_spoken_reply import decode_spoken_response
from tests.test_meet_speech_publication import Browser
from tests.test_meet_spoken_reply_contract import NOW, packet
from worker.meet_media.dialog_chat import DialogChatPump
from worker.meet_media.dialog_speech_output import DialogSpeechOutput, speech_binding


def fixture():
    request, expected, value = packet(4859)
    assignment = {
        **expected,
        "deadline": NOW + 60,
        "capabilities": ["chat.read", "chat.send", "speech.publish"],
        "meeting": {"origin": "https://meet.example.test", "room_id": expected["room_id"]},
    }
    receipt = {
        "lease": {"sessionId": expected["meet_session_id"], "generation": 2, "expiresAt": (NOW + 60) * 1000},
        "roomId": expected["room_id"],
        "peerId": "machine",
        "membershipEpoch": 3,
        "receiveRevision": 4,
        "grants": [{"chatRead": True, "publisherPeerId": "human", "expiresAt": (NOW + 60) * 1000}],
    }
    controls = {name: {"enabled": True, "revision": 1, "since": NOW * 1000} for name in ("chat", "speech")}
    page = Mock(url=assignment["meeting"]["origin"] + "/machine")
    local = {"joined": True, "lease": copy.deepcopy(receipt["lease"]), "chat": True}
    page.evaluate.side_effect = lambda expression, *args: local if "const {joined, lease}" in expression else True
    browser = Browser()
    elapsed = [100]
    output = DialogSpeechOutput(page, assignment, browser=browser, clock=lambda: NOW, monotonic=lambda: elapsed[0])
    output.update(receipt, controls)
    result = decode_spoken_response(value, request, expected, NOW * 1000)
    return SimpleNamespace(**locals())


def test_worker_projection_matches_hub_envelope_and_bounded_pcm_completes():
    f = fixture()
    binding = f.output.prepare(f.request["event"])
    assert binding == f.expected
    assert f.output.accept(f.result, binding)
    f.output.tick()
    assert f.browser.sent == 4410 and f.output.busy
    f.output.tick()
    assert len(f.browser.frames) == 10  # Backpressure, no duplicate frame.
    f.browser.played = 4410
    f.output.tick()
    assert f.browser.sent == 4859 and f.browser.frames[-1][0] == 4851
    f.browser.transform_status = lambda _: {
        "state": "completed",
        "generation": 2,
        "receivedSamples": 4859,
        "playedSamples": 4859,
        "bufferedSamples": 0,
    }
    f.output.tick()
    assert not f.output.busy and f.output.pcm == b"" and f.output.binding is None
    f.output.tick()
    assert len(f.browser.frames) == 12


@pytest.mark.parametrize(
    "change",
    [
        "speech",
        "chat",
        "receive",
        "membership",
        "generation",
        "grant",
        "capability",
        "expired",
        "stale",
        "navigation",
        "browser",
        "invalidate",
    ],
)
def test_control_and_browser_revocation_clear_only_owned_speech_without_reopen(change):
    f = fixture()
    assert f.output.accept(f.result, f.expected)
    f.output.tick()
    before = len(f.browser.frames)
    if change in {"speech", "chat"}:
        f.controls[change]["revision"] += 1  # Includes pause/resume ABA.
    elif change == "receive":
        f.receipt["receiveRevision"] += 1
    elif change == "membership":
        f.receipt["membershipEpoch"] += 1
    elif change == "generation":
        f.receipt["lease"]["generation"] += 1
    elif change == "grant":
        f.receipt["grants"].clear()
    elif change == "capability":
        f.assignment["capabilities"].remove("speech.publish")
    elif change == "expired":
        f.output.clock = lambda: NOW + 60
    elif change == "stale":
        f.elapsed[0] += 2.5
    elif change == "navigation":
        f.page.url += "/other"
    elif change == "browser":
        f.local["chat"] = False
        f.elapsed[0] += 0.05
    else:
        f.output.invalidate()
    f.output.tick()
    assert not f.output.busy and not f.output.pcm and len(f.browser.frames) == before
    f.output.tick()
    assert f.browser.closed == [1]
    assert all(
        "chat.close" not in call.args[0] and "screen" not in call.args[0] for call in f.page.evaluate.call_args_list
    )


def test_legacy_and_paused_speech_keep_text_path_but_stale_input_is_not_upgraded():
    f = fixture()
    f.controls["speech"]["enabled"] = False
    assert f.output.prepare(f.request["event"]) is None
    del f.controls["speech"]
    assert f.output.prepare(f.request["event"]) is None
    f.controls["speech"] = {"enabled": True, "revision": 2, "since": NOW * 1000 + 1}
    with pytest.raises(ValueError, match="input_stale"):
        f.output.prepare(f.request["event"])
    assert not f.output.accept(f.result, f.expected)
    assert f.browser.frames == []


def test_failed_source_setup_cannot_reopen_on_tick():
    f = fixture()
    f.browser.transform_receipt = lambda value: value | {"sourceId": "foreign"}
    assert not f.output.accept(f.result, f.expected)
    f.output.tick()
    assert f.browser.closed == [1] and not f.output.pcm and not f.browser.frames


def test_browser_checkpoint_cache_is_only_fifty_ms_and_never_caches_hub_policy():
    f = fixture()
    assert f.output.accept(f.result, f.expected)
    before = f.page.evaluate.call_count
    f.output.tick()
    assert f.page.evaluate.call_count == before  # Ten PCM frames, no redundant authority RPCs.
    f.local["chat"] = False
    f.elapsed[0] += 0.05
    with pytest.raises(ValueError, match="browser_changed"):
        f.output.require_current()
    f.local["chat"] = True
    f.output.require_current()
    f.controls["speech"]["enabled"] = False
    with pytest.raises(ValueError):
        f.output.require_current()  # Hub control checks run even within the cache interval.
    f.output.close()


@pytest.mark.parametrize("change", ["none", "speech", "chat", "generation", "send_failure", "reopened"])
def test_chat_waits_for_post_generation_hub_exchange_before_text_or_pcm(change):
    f = fixture()
    hub = Mock()
    pump = DialogChatPump(f.page, hub, f.assignment, speech=f.output)
    pump.opened, pump.receipt, pump.revision = {"generation": 2}, f.receipt, 1
    pump.browser_chat = Mock()
    pump.browser_chat.poll.return_value = {"events": []}
    future = Future()
    future.set_result(f.result)
    pump.pending = (future, pump.opened, f.result.message_id, 1)
    pump.pending_speech = f.expected
    try:
        pump.tick()
        assert pump.needs_refresh and not f.output.busy and not f.browser.frames
        pump.tick()
        assert pump.pending is not None and not f.output.busy
        if change in {"speech", "chat"}:
            f.controls[change]["revision"] += 1
        elif change == "generation":
            f.receipt["lease"]["generation"] += 1
        elif change == "reopened":
            pump.opened = dict(pump.opened)  # Equal values are not the old activation.
        elif change == "send_failure":
            f.page.evaluate.side_effect = (
                lambda expression, *args: f.local if "const {joined, lease}" in expression else False
            )
        f.output.update(f.receipt, f.controls)
        pump.tick()
        assert pump.pending is None and not pump.needs_refresh
        f.output.tick()
        if change == "none":
            assert f.output.busy and f.browser.sent == 4410
        else:
            assert not f.output.busy and not f.browser.frames
        hub.call.assert_not_called()
        hub.spoken.assert_not_called()
    finally:
        pump.close()


def test_worker_deadline_uses_all_read_grants_like_current_hub_authority():
    f = fixture()
    f.receipt["grants"].append({"chatRead": True, "publisherPeerId": "other", "expiresAt": (NOW + 3) * 1000})
    assert speech_binding(f.assignment, f.receipt, f.controls, "human")["deadline_ms"] == (NOW + 3) * 1000


@pytest.mark.parametrize("mode", ["spoken", "paused", "legacy"])
def test_one_acked_input_uses_exactly_one_existing_hub_request_pool(mode):
    f = fixture()
    if mode == "paused":
        f.controls["speech"]["enabled"] = False
    elif mode == "legacy":
        del f.controls["speech"]
    hub = Mock()
    pump = DialogChatPump(f.page, hub, f.assignment, speech=f.output)
    pump.pool.shutdown(wait=True)
    pump.pool = Mock()
    future = Future()
    pump.pool.submit.return_value = future
    pump.opened, pump.receipt, pump.revision = {"generation": 2}, f.receipt, 1
    pump.browser_chat = Mock()
    pump.browser_chat.poll.return_value = {"events": [{"cursor": 1, "event": f.request["event"]}]}
    try:
        pump.tick()
        pump.browser_chat.ack.assert_called_once_with(1)
        if mode == "spoken":
            pump.pool.submit.assert_called_once_with(hub.spoken, f.request["event"], f.expected)
        else:
            pump.pool.submit.assert_called_once_with(
                hub.call, "chat", meet_session_id=f.expected["meet_session_id"], event=f.request["event"]
            )
        pump.tick()
        assert pump.pool.submit.call_count == 1
    finally:
        pump.close()
    future.set_result(f.result)
    assert pump.pending is None and not f.output.busy and not f.output.pcm
