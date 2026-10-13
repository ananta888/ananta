"""Shared timeline plus actual source gates under explicit synthetic Hub reads."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet.test_meet_companion_task_controls import fixture
from tests.meet.test_meet_snake_avatar_sync import FakeClock, fake_encoder, fake_ports, pcm_bytes, tone
from worker.meet_media.companion_media import SpeechAvatarPublisher
from worker.meet_media.companion_task_controls import CompanionControlLost, CompanionSourceInterrupted
from worker.meet_media.companion_task_speech import AssignedCompanionSpeech


def publication(*, avatar=True, update=lambda _clock, _state: None):
    clock = FakeClock()
    now, controls, state, _pool = fixture()
    state["controls"]["avatar"]["enabled"] = avatar
    controls.state["controls"]["avatar"]["enabled"] = avatar
    ports, events = fake_ports(clock)

    def cooperate():
        now[0] = clock()
        update(clock, state)
        controls.step()
        pending = controls.exchange.pending
        if pending is not None and not pending.done():
            pending.set_result(state)
            controls.step()

    media = SimpleNamespace(speaking=Mock(side_effect=lambda value, cooperate: (
        cooperate(), SpeechAvatarPublisher(ports, encoder=fake_encoder).prepare(value)[1])[1]))
    publisher = AssignedCompanionSpeech(controls, media, ports, cooperate=cooperate)
    return SimpleNamespace(**locals())


def assert_audio_complete(events, pcm):
    audio = [item for item in events if item[0] == "audio"]
    assert audio[0][2] == 0 and audio[-1][2] + 441 >= len(pcm) // 2
    assert [item[2] for item in audio] == list(range(0, len(pcm) // 2, 441))


def test_assigned_long_speech_uses_one_pcm_timeline_and_cooperative_clip_preparation():
    f = publication()
    pcm = pcm_bytes(tone(21))
    try:
        generation, report = f.publisher.publish(pcm, current_generation=7)
        assert generation == 3 and len(report["segments"]) == 3
        assert report["max_drift_us"] <= 41_667 and report.get("sync_complete", True)
        assert len([item for item in f.events if item[0] == "video"]) == 3
        assert_audio_complete(f.events, pcm)
        f.media.speaking.assert_called_once()
    finally:
        f.controls.close()


def test_disabled_avatar_does_not_render_or_publish_clips_and_speech_still_completes():
    f = publication(avatar=False)
    pcm = pcm_bytes(tone(.5))
    try:
        generation, report = f.publisher.publish(pcm)
        assert generation is None and report["sync_complete"] is False and report["retired_segments"] == 1
        assert report["segments"] == []
        assert not any(item[0] == "video" for item in f.events)
        f.media.speaking.assert_not_called()
        assert_audio_complete(f.events, pcm)
    finally:
        f.controls.close()


def test_avatar_revoked_during_preparation_cannot_open_prepared_video_or_stop_speech():
    f = publication()
    prepare = f.media.speaking.side_effect

    def revoked(value, *, cooperate):
        clips = prepare(value, cooperate)
        f.state["controls"]["revision"] = 2
        f.state["controls"]["avatar"] = {"enabled": False, "revision": 2, "since": 2000}
        f.clock.now += 1
        cooperate()
        return clips

    f.media.speaking.side_effect = revoked
    pcm = pcm_bytes(tone(.5))
    try:
        _generation, report = f.publisher.publish(pcm)
        assert report["sync_complete"] is False
        assert not any(item[0] == "video" for item in f.events)
        assert_audio_complete(f.events, pcm)
    finally:
        f.controls.close()


def test_avatar_pause_and_resume_cannot_reuse_old_utterance_checkpoint():
    def update(clock, state):
        revision = 3 if clock() >= 101.5 else 2 if clock() >= 100.2 else 1
        if state["controls"]["revision"] != revision:
            state["controls"]["revision"] = revision
            state["controls"]["avatar"] = {"enabled": revision != 2, "revision": revision, "since": revision * 1000}

    f = publication(update=update)
    pcm = pcm_bytes(tone(21))
    try:
        _generation, report = f.publisher.publish(pcm)
        assert len([item for item in f.events if item[0] == "video"]) == 1
        assert report["sync_complete"] is False and report["retired_segments"] == 2
        assert_audio_complete(f.events, pcm)
        f.controls.checkpoint("avatar")  # Resume permits a new operation only.
    finally:
        f.controls.close()


def test_retiring_an_already_open_single_clip_is_not_reported_as_complete_sync():
    def update(clock, state):
        if clock() >= 100.2:
            state["controls"]["revision"] = 2
            state["controls"]["avatar"] = {"enabled": False, "revision": 2, "since": 2000}

    f = publication(update=update)
    pcm = pcm_bytes(tone(2.5))
    try:
        _generation, report = f.publisher.publish(pcm)
        assert len([item for item in f.events if item[0] == "video"]) == 1
        assert report["sync_complete"] is False and report["avatar_retired"] is True
        assert_audio_complete(f.events, pcm)
    finally:
        f.controls.close()


def test_speech_revocation_stops_remaining_audio_frames():
    def update(clock, state):
        if clock() >= 100.2:
            state["controls"]["revision"] = 2
            state["controls"]["speech"] = {"enabled": False, "revision": 2, "since": 2000}

    f = publication(update=update)
    pcm = pcm_bytes(tone(3))
    try:
        with pytest.raises(CompanionSourceInterrupted):
            f.publisher.publish(pcm)
        audio = [item for item in f.events if item[0] == "audio"]
        assert audio and audio[-1][2] + 441 < len(pcm) // 2
        f.controls.checkpoint("avatar")
    finally:
        f.controls.close()


def test_global_hub_denial_is_never_treated_as_optional_video_retirement():
    f = publication()
    cooperate = f.publisher.cooperate

    def denied():
        if f.clock() >= 100.2:
            raise CompanionControlLost("synthetic_hub_denial")
        cooperate()

    f.publisher.cooperate = denied
    try:
        with pytest.raises(CompanionControlLost):
            f.publisher.publish(pcm_bytes(tone(3)))
    finally:
        f.controls.close()
