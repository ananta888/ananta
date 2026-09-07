from unittest.mock import Mock

import pytest

from tests.meet_dialog_voice_scenario import make_voice_scenario
from tests.meet_speech_playback_delay import SpeechPlaybackDelay
from worker.meet_media.browser_pcm_feeder import BrowserPcmFeeder


def test_delay_changes_only_poll_timing_and_bounds_its_passive_report(monkeypatch):
    elapsed = [0.0]
    original = Mock(return_value={"synthetic": True})
    monkeypatch.setattr(BrowserPcmFeeder, "status", original)
    probe = SpeechPlaybackDelay(monkeypatch, clock=lambda: elapsed[0])
    page = Mock(url="https://meet.test/machine")
    page.wait_for_timeout.side_effect = lambda ms: elapsed.__setitem__(0, elapsed[0] + ms / 1000)
    feeder = BrowserPcmFeeder(page, page.url)
    for _ in range(140):
        assert feeder.status() == {"synthetic": True}
    assert original.call_count == 140 and page.wait_for_timeout.call_count == 140
    page.evaluate.assert_not_called()
    record = Mock()
    probe.verify(record)
    assert record.call_args.args == ("synthetic_python_playback_poll_delay_ms", [350.0] * 128)


def test_delay_case_cannot_be_misclassified_as_real_gpu(monkeypatch):
    with pytest.raises(ValueError, match="requires_synthetic_audio"):
        make_voice_scenario("playback-latency", Mock(), monkeypatch, actual_gpu=True)
