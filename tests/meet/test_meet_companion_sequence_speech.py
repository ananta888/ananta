"""Assigned checkpoints retain native quality receipts; no host timing substitutes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet.test_meet_companion_task_controls import fixture
from tests.meet.test_meet_snake_avatar_sync import FakeClock, fake_ports, pcm_bytes, tone
from worker.meet_media.companion_task_controls import CompanionControlLost
from worker.meet_media.companion_task_speech import AssignedCompanionSpeech


def setup():
    _now, controls, _state, _pool = fixture()
    ports, events = fake_ports(FakeClock())
    media = Mock()
    media.speaking.return_value = [{"synthetic": 1}, {"synthetic": 2}]
    report = {"schema": "ananta.meet-avatar-sequence-quality.v1", "id": 1,
        "segments": [{"segment": 0, "expected_start_sample": 0, "observed_played_sample": 0, "drift_us": 0},
                     {"segment": 1, "expected_start_sample": 220500,
                      "observed_played_sample": 220941, "drift_us": 20000}],
        "max_drift_us": 20000, "sync_complete": True}
    sequence = SimpleNamespace(prepare=Mock(return_value=1), activate=Mock(),
                               quality=Mock(return_value=report), cancel=Mock())
    publication = AssignedCompanionSpeech(controls, media, ports, cooperate=lambda: None, sequence=sequence)
    return publication, sequence, controls, events, report


def test_native_receipt_owns_multiclip_quality_and_host_never_swaps_video():
    publication, sequence, controls, events, _report = setup()
    try:
        _generation, result = publication.publish(pcm_bytes(tone(12)))
        assert result["sync_complete"] and result["max_drift_us"] == 20000
        assert not [event for event in events if event[0] == "video"]
        sequence.activate.assert_called_once()
        sequence.cancel.assert_called_once()
        content = sequence.prepare.call_args.args[0]
        assert content["totalSamples"] == 264600
        assert [row["startSample"] for row in content["clips"]] == [0, 220500]
    finally:
        controls.close()


@pytest.mark.parametrize("invalid", ["fake_maximum", "wrong_window", "wrong_generation", "unknown_field"])
def test_bad_native_quality_cannot_become_a_green_host_report(invalid):
    publication, sequence, controls, _events, report = setup()
    if invalid == "fake_maximum":
        report["max_drift_us"] = 0
    elif invalid == "wrong_window":
        report["segments"][1]["expected_start_sample"] = 220501
    elif invalid == "wrong_generation":
        report["id"] = 2
    else:
        report["approval"] = True
    try:
        with pytest.raises(ValueError, match="native_sequence"):
            publication.publish(pcm_bytes(tone(12)))
        sequence.cancel.assert_called_once()
    finally:
        controls.close()


def test_native_activation_failure_does_not_downgrade_to_host_clip_swaps():
    publication, sequence, controls, events, _report = setup()
    sequence.activate.side_effect = ValueError("synthetic_native_source_failed")
    try:
        with pytest.raises(ValueError, match="synthetic_native_source_failed"):
            publication.publish(pcm_bytes(tone(12)))
        assert not [event for event in events if event[0] == "video"]
        sequence.cancel.assert_called_once()
    finally:
        controls.close()


def test_explicit_avatar_retirement_during_prepare_keeps_only_audio_live():
    publication, sequence, controls, events, _report = setup()
    def retire(*_args, **kwargs):
        controls.state["controls"]["avatar"] = {"enabled": False, "revision": 2, "since": 2000}
        kwargs["cooperate"]()
        raise ValueError("synthetic_retired_native_preparation")
    sequence.prepare.side_effect = retire
    try:
        _generation, result = publication.publish(pcm_bytes(tone(12)))
        assert result["sync_complete"] is False
        assert [event for event in events if event[0] == "audio"]
        sequence.activate.assert_not_called()
        sequence.quality.assert_not_called()
    finally:
        controls.close()


def test_global_hub_loss_during_prepare_never_publishes_audio_or_old_video():
    publication, sequence, controls, events, _report = setup()
    def revoke(*_args, **kwargs):
        controls.close()
        kwargs["cooperate"]()
    sequence.prepare.side_effect = revoke
    with pytest.raises(CompanionControlLost):
        publication.publish(pcm_bytes(tone(12)))
    assert not [event for event in events if event[0] in {"audio", "video"}]
    sequence.cancel.assert_called_once()
