"""One-use current Hub permits fence actual Worker PCM publication operations."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from tests.test_meet_dialog_speech_output import fixture
from tests.test_meet_speaker_contract import permit
from tests.test_meet_spoken_reply_contract import NOW
from worker.meet_media.dialog_speech_output import DialogSpeechOutput


def output():
    f = fixture()
    f.assignment["speaker_floor"] = True
    f.finished = Mock()
    f.output = DialogSpeechOutput(
        f.page,
        f.assignment,
        browser=f.browser,
        clock=lambda: NOW,
        monotonic=lambda: f.elapsed[0],
        finished=f.finished,
    )
    f.result = replace(f.result, speaker_floor=permit())
    return f


def test_reply_requires_a_fresh_matching_control_projection_before_opening_pcm():
    f = output()
    f.output.update(f.receipt, f.controls, speaker_floor=None)
    assert f.output.prepare(f.request["event"]) == f.expected
    assert not f.output.accept(f.result, f.expected)
    assert not f.browser.frames and not f.output.busy
    f.finished.assert_not_called()
    f.output.update(f.receipt, f.controls, speaker_floor=permit())
    assert f.output.accept(f.result, f.expected)
    assert f.output._playback_authority()["deadline"] == permit()["expires_ms"]
    f.output.tick()
    assert f.browser.sent > 0


@pytest.mark.parametrize("change", [None, {"sequence": 2}, {"id": "b" * 64}, {"expires_ms": (NOW + 31) * 1000}])
def test_withdrawal_or_other_speaker_clears_pcm_and_cannot_restore_consumed_permit(change):
    f = output()
    f.output.update(f.receipt, f.controls, speaker_floor=permit())
    assert f.output.accept(f.result, f.expected)
    f.output.tick()
    before = f.browser.sent
    updated = None if change is None else permit() | change
    f.output.update(f.receipt, f.controls, speaker_floor=updated)
    assert not f.output.busy and not f.output.pcm
    f.finished.assert_called_once_with(permit())
    f.output.update(f.receipt, f.controls, speaker_floor=permit())
    assert not f.output.accept(f.result, f.expected)
    f.output.tick()
    assert f.browser.sent == before
    assert f.finished.call_count == 1


def test_floor_deadline_stops_pcm_even_if_ordinary_speech_and_hub_state_are_current():
    f = output()
    f.output.update(f.receipt, f.controls, speaker_floor=permit())
    assert f.output.accept(f.result, f.expected)
    f.output.tick()
    before = f.browser.sent
    f.output.speaker_gate.clock = lambda: NOW + 30
    f.output.tick()
    assert not f.output.busy and not f.output.pcm and f.browser.sent == before
    f.finished.assert_called_once_with(permit())


def test_malformed_state_fails_closed_and_stale_state_stops_the_negotiated_source():
    f = output()
    f.output.update(f.receipt, f.controls, speaker_floor=permit())
    assert f.output.accept(f.result, f.expected)
    with pytest.raises(ValueError, match="permit_invalid"):
        f.output.update(f.receipt, f.controls, speaker_floor=permit() | {"priority": 2})
    assert not f.output.busy
    fresh = permit() | {"id": "b" * 64, "sequence": 2}
    f.output.update(f.receipt, f.controls, speaker_floor=fresh)
    assert f.output.accept(replace(f.result, speaker_floor=fresh), f.expected)
    f.elapsed[0] += 2.5
    f.output.tick()
    assert not f.output.busy and f.finished.call_count == 2


def test_legacy_worker_never_accepts_unnegotiated_permit():
    f = fixture()
    assert not f.output.accept(replace(f.result, speaker_floor=permit()), f.expected)
    with pytest.raises(ValueError, match="not_negotiated"):
        f.output.update(f.receipt, f.controls, speaker_floor=permit())


def test_uncertain_source_cleanup_does_not_report_silence_or_reopen_old_permit():
    f = output()
    f.output.update(f.receipt, f.controls, speaker_floor=permit())
    assert f.output.accept(f.result, f.expected)
    f.output.publication.close = Mock(side_effect=ValueError("synthetic_cleanup_failed"))
    with pytest.raises(ValueError, match="cleanup_failed"):
        f.output.close()
    f.finished.assert_not_called()
    assert not f.output.pcm and not f.output.busy
    assert not f.output.accept(f.result, f.expected)
    f.finished.assert_not_called()
