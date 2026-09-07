"""Pending source setup must leave the assigned runtime free for fresh Hub reads."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_speech_output import fixture
from tests.test_meet_dialog_voice_projection import ready
from worker.meet_media.dialog_speech_output import DialogSpeechOutput
from worker.meet_media.speech_opening import BrowserSpeechOpening


def setup():
    f = fixture()
    state = SimpleNamespace(ready=False, receipt=None)
    original_open = f.browser.open

    def begin(source_id, total_samples):
        state.receipt = original_open(source_id, total_samples)
        return "a" * 32

    f.browser.begin_open = Mock(side_effect=begin)
    f.browser.poll_open = Mock(
        side_effect=lambda token: {
            "state": "done" if state.ready else "pending",
            "result": state.receipt if state.ready else None,
        }
    )
    f.browser.cancel_open = Mock()
    f.browser.open = Mock(side_effect=AssertionError("blocking source open forbidden"))
    f.output = DialogSpeechOutput(
        f.page,
        f.assignment,
        browser=f.browser,
        clock=f.output.clock,
        monotonic=lambda: f.elapsed[0],
        opening_factory=BrowserSpeechOpening,
    )
    f.output.update(f.receipt, f.controls)
    return f, state


def test_three_second_start_spans_fresh_hub_updates_without_inner_wait_or_extra_source():
    f, state = setup()
    assert f.output.accept(f.result, f.expected) and f.output.busy
    f.browser.begin_open.assert_not_called()  # Chat can reserve correlation first.
    for tick in range(35):
        f.elapsed[0] = 100 + tick / 10
        if tick % 10 == 0:
            f.output.update(f.receipt, f.controls)
        f.output.tick()
        assert f.output.busy and f.output.publication is None and not f.browser.frames
        assert f.browser.begin_open.call_count + f.browser.poll_open.call_count == tick + 1
    f.browser.begin_open.assert_called_once()
    f.browser.open.assert_not_called()
    f.page.wait_for_timeout.assert_not_called()
    assert not f.output.accept(f.result, f.expected)  # No second start/backlog.
    state.ready = True
    f.output.tick()
    assert f.output.opening is None and f.output.publication is not None and f.browser.sent == 4410
    f.browser.cancel_open.assert_not_called()
    f.output.close()
    assert not f.output.busy and not f.output.pcm and f.browser.closed == [1]
    f.browser.cancel_open.assert_not_called()  # Ownership transferred exactly once.


@pytest.mark.parametrize(
    "change", ["pause", "revision", "chat", "generation", "navigation", "stale", "invalidate", "timeout"]
)
def test_pending_setup_is_cancelled_on_revocation_and_late_completion_cannot_reopen(change):
    f, state = setup()
    assert f.output.accept(f.result, f.expected)
    f.output.tick()
    if change == "pause":
        f.controls["speech"]["enabled"] = False
    elif change == "revision":
        f.controls["speech"]["revision"] += 1
    elif change == "chat":
        f.controls["chat"]["enabled"] = False
    elif change == "generation":
        f.receipt["lease"]["generation"] += 1
    elif change == "navigation":
        f.page.url += "/other"
    elif change == "stale":
        f.elapsed[0] += 2.5
    elif change == "invalidate":
        f.output.invalidate()
    elif change == "timeout":
        f.elapsed[0] += 10
        f.output.update(f.receipt, f.controls)  # Fresh authority cannot extend the setup budget.
    f.output.tick()
    assert not f.output.busy and not f.output.pcm and f.output.binding is None
    f.browser.cancel_open.assert_called_once_with("a" * 32)
    state.ready = True
    f.output.tick()
    assert not f.browser.frames
    f.browser.begin_open.assert_called_once()


@pytest.mark.parametrize(
    "bad_phase",
    [
        None,
        {},
        {"state": "pending", "result": {}},
        {"state": "failed", "result": None},
        {"state": "done", "result": {}},
    ],
)
def test_bad_or_malformed_source_receipt_cancels_without_using_synchronous_fallback(bad_phase):
    f, _ = setup()
    assert f.output.accept(f.result, f.expected)
    f.output.tick()
    f.browser.poll_open.side_effect = None
    f.browser.poll_open.return_value = bad_phase
    f.output.tick()
    assert not f.output.busy and not f.output.pcm and not f.browser.frames
    f.browser.open.assert_not_called()
    f.browser.cancel_open.assert_called_once_with("a" * 32)


def test_setup_receipt_is_delivered_once_and_cannot_be_released_before_ready():
    browser = Mock()
    browser.begin_open.return_value = "a" * 32
    browser.poll_open.return_value = {"state": "done", "result": {"synthetic": "receipt"}}
    opening = BrowserSpeechOpening(browser, "speech:synthetic", 441, lambda: None)
    with pytest.raises(ValueError, match="not_delivered"):
        opening.release()
    assert opening.poll() is None
    assert opening.poll() == {"synthetic": "receipt"}
    with pytest.raises(ValueError, match="already_delivered"):
        opening.poll()
    opening.release()
    opening.close()
    browser.cancel_open.assert_not_called()


def test_voice_revocation_and_ready_aba_cannot_revive_pending_setup_or_its_pcm():
    f, state = setup()
    f.assignment["voice_profiles"] = True
    voice = ready()
    f.output.update(f.receipt, f.controls, voice)
    binding = f.output.prepare(f.request["event"])
    assert f.output.accept(f.result, binding)
    f.output.tick()
    f.output.update(f.receipt, f.controls, voice | {"state": "blocked", "profile": None})
    f.output.update(f.receipt, f.controls, voice)
    state.ready = True
    assert not f.output.accept(f.result, binding)
    f.output.tick()
    assert not f.output.busy and not f.output.pcm and not f.browser.frames
    f.browser.cancel_open.assert_called_once_with("a" * 32)


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("pcm", [None, bytearray(b"\x00\x00"), b"", b"\x00", bytes(40 * 22050 * 2 + 2)])
def test_malformed_pcm_never_reaches_browser_or_reserves_pending_source(deferred, pcm):
    f, _ = setup()
    if not deferred:
        f.output.opening_factory = None
    assert not f.output.accept(replace(f.result, pcm=pcm), f.expected)
    assert not f.output.busy and not f.output.pcm and f.output.binding is None
    f.browser.begin_open.assert_not_called()
    f.browser.open.assert_not_called()
    f.output.tick()
    assert not f.browser.frames
