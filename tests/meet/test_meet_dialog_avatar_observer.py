"""Fixture classification never replaces a real Worker with synthetic speech."""

from types import SimpleNamespace

import pytest

from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_avatar_observer import DialogAvatarObserver, configure_avatar_speech
from tests.meet_dialog_interruption import SyntheticToneWorker


def test_explicit_gpu_mode_preserves_the_configured_worker_and_profile():
    worker, profile = object(), speech_profile(max_seconds=20)
    speech = SimpleNamespace(worker=worker, profile=profile)
    configure_avatar_speech(speech, True)
    assert speech.worker is worker and speech.profile is profile


@pytest.mark.parametrize("worker,seconds", [(None, 20), (object(), 10)])
def test_gpu_mode_without_its_provisioned_transport_profile_fails_closed(worker, seconds):
    with pytest.raises(ValueError, match="gpu_not_configured"):
        configure_avatar_speech(SimpleNamespace(worker=worker, profile=speech_profile(max_seconds=seconds)), True)


def test_synthetic_mode_is_explicit_and_cannot_silently_replace_an_existing_worker():
    speech = SimpleNamespace(worker=None, profile=None)
    configure_avatar_speech(speech, False)
    assert isinstance(speech.worker, SyntheticToneWorker)
    assert speech.profile == speech_profile(max_seconds=10)
    worker = speech.worker
    with pytest.raises(ValueError, match="classification_conflict"):
        configure_avatar_speech(speech, False)
    assert speech.worker is worker


def test_disabled_avatar_does_not_change_an_ordinary_gpu_speech_case(monkeypatch):
    speech = SimpleNamespace(worker=object(), profile=speech_profile(max_seconds=20), capabilities=["speech.publish"])
    worker, profile = speech.worker, speech.profile
    observer = DialogAvatarObserver(False, speech, monkeypatch, actual_gpu=True)
    assert observer.enabled is False and speech.worker is worker and speech.profile is profile
    assert speech.capabilities == ["speech.publish"]
