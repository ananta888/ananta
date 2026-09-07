"""Small deterministic interruption-fixture contracts, not live/GPU evidence."""

import base64
import io
import wave
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_speech_result import validate_speech_binding
from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_interruption import (
    SAMPLES,
    HubInterruptionControl,
    SpeechInterruption,
    SyntheticToneWorker,
    configure_interruption,
    finish_interruption,
)
from tests.meet_dialog_policy_fixture import SyntheticMeetBinding
from tests.test_meet_dialog_speech_output import fixture


def test_tone_is_deterministic_non_silent_and_bound_to_the_delegated_profile():
    turn = {"task_id": "task", "lease_id": "lease", "speech_profile": speech_profile(max_seconds=10)}
    first = SyntheticToneWorker().execute(turn)
    assert SyntheticToneWorker().execute(turn) == first
    validate_speech_binding(turn, first)
    assert first["task_id"] == "task" and first["lease_id"] == "lease"
    with wave.open(io.BytesIO(base64.b64decode(first["audio"]["base64"])), "rb") as audio:
        assert (audio.getnframes(), audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (
            SAMPLES,
            22050,
            1,
            2,
        )
        pcm = audio.readframes(SAMPLES)
    assert len(pcm) == SAMPLES * 2 and any(pcm)


def test_pause_uses_actual_current_cas_and_preserves_unrelated_controls():
    service = Mock()
    controls = {
        "revision": 7,
        **{
            name: {"enabled": enabled}
            for name, enabled in (("speech", True), ("screen", True), ("chat", True), ("audio", False))
        },
    }
    service.inspect.return_value = {"controls": controls}
    caller = HubInterruptionControl(SimpleNamespace(app_context=nullcontext), service, "principal", "task")
    assert caller.pause() is service.control.return_value
    service.control.assert_called_once_with(
        "principal",
        "synthetic",
        "task",
        {"expected_revision": 7, "chat": True, "audio": False, "screen": True, "speech": False},
    )
    caller.stop()
    assert service.inspect.call_args.kwargs == {"stop": True}


def test_partial_close_records_counts_and_cleared_ownership_without_media(monkeypatch):
    f = fixture()
    scenario = SpeechInterruption("pause", monkeypatch, clock=lambda: 50)
    assert f.output.accept(f.result, f.expected)
    f.output.tick()
    f.browser.played = 4410
    f.output.tick()
    assert scenario.live == {"played": 4410, "total": 4859}
    f.output.close()
    f.output.close()
    assert scenario.closed == [{"at": 50, "played": 4410, "sent": 4859, "total": 4859, "cleared": True}]
    assert scenario.live is None and not f.output.pcm


def test_normal_completion_cannot_be_counted_as_an_interruption(monkeypatch):
    f = fixture()
    scenario = SpeechInterruption("stop", monkeypatch)
    assert f.output.accept(f.result, f.expected)
    f.output.publication.completed = True
    f.output.close()
    assert scenario.closed == []


def test_normal_modes_do_not_enable_interruption_or_synthetic_inference(monkeypatch):
    observer = SimpleNamespace(worker=None, profile=None)
    assert configure_interruption(observer, None, monkeypatch) is None
    assert observer.worker is None and observer.profile is None
    assert not finish_interruption(None, None, None, None, None, None, None, None, None, None)
    with pytest.raises(ValueError, match="mode_invalid"):
        configure_interruption(observer, "unknown", monkeypatch)
    assert observer.worker is None


def test_extracted_synthetic_policy_keeps_exact_project_and_principal_boundary():
    binding = SyntheticMeetBinding("https://meet.example.test", "room-" + "a" * 18, "owner")
    assert binding.read("owner", "synthetic") == {"invite_url": binding.profile.invite(binding.room_id)}
    for actor, project in (("other", "synthetic"), ("owner", "other")):
        with pytest.raises(MeetError, match="test_scope_denied"):
            binding.read(actor, project)
