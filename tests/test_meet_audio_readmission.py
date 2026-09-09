"""A source reservation refusal must not terminate unrelated dialog sources."""

import time
from unittest.mock import Mock

import pytest

from worker.meet_media.dialog_runtime import start_audio


@pytest.mark.parametrize("close_failure", [False, True])
def test_refused_audio_readmission_closes_source_without_starting_or_killing_dialog(monkeypatch, close_failure):
    page, hub, pump = Mock(), Mock(), Mock()
    source = {"publicationId": "microphone", "peerId": "a" * 16}

    def evaluate(expression):
        if "sources" in expression:
            return [source]
        assert expression == "window.anantaMachine.audio.close()"
        if close_failure:
            raise RuntimeError("synthetic lost browser")

    page.evaluate.side_effect = evaluate
    hub.call.side_effect = ValueError("meet_dialog_hub_revoked_or_unavailable")
    monkeypatch.setattr("worker.meet_media.dialog_audio.DialogAudioPump", pump)
    state = {
        "controls": {"audio": {"enabled": True}},
        "audio_job": None,
        "authorization": {"lease": {"expiresAt": (time.time() + 90) * 1000}, "publications": [source]},
    }
    assert start_audio(page, hub, {"audio_mode": "transcribe"}, state, "synthetic-session") is None
    pump.assert_not_called()
    hub.call.assert_called_once_with("audio", meet_session_id="synthetic-session", publication_id="microphone")
    assert page.evaluate.call_args.args == ("window.anantaMachine.audio.close()",)
