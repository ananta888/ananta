from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_voice_scenario import make_voice_scenario
from tests.meet_speech_playback_stall import PlaybackStallScenario
from worker.meet_media.browser_pcm_feeder import BrowserPcmFeeder


def test_stall_runs_once_only_after_an_armed_successful_current_hub_pulse(monkeypatch):
    pulse = Mock(return_value=True)
    status = Mock(return_value={"state": "failed"})
    monkeypatch.setattr(BrowserPcmFeeder, "pulse", pulse)
    monkeypatch.setattr(BrowserPcmFeeder, "status", status)
    speech = SimpleNamespace(enabled=True, worker=None, profile=None)
    scenario = PlaybackStallScenario(speech, monkeypatch)
    page = Mock(url="https://meet.test/machine")
    feeder = BrowserPcmFeeder(page, page.url)
    feeder.pulse({})
    feeder.status()
    page.wait_for_timeout.assert_not_called()
    scenario.armed = True
    pulse.return_value = False
    feeder.pulse({})
    feeder.status()
    page.wait_for_timeout.assert_not_called()
    pulse.return_value = True
    feeder.pulse({})
    feeder.status()
    feeder.status()
    feeder.pulse({})
    feeder.status()
    page.wait_for_timeout.assert_called_once_with(4500)
    assert scenario.started.is_set() and scenario.returned.is_set() and scenario.source_state == "failed"
    assert not scenario.armed and not scenario.ready
    page.evaluate.assert_not_called()


def test_worker_stall_never_masquerades_as_gpu_acceptance(monkeypatch):
    with pytest.raises(ValueError, match="requires_synthetic_audio"):
        make_voice_scenario("playback-stall", Mock(), monkeypatch, actual_gpu=True)
