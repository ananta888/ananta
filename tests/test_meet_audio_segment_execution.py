"""Real PCM receiver and segment detector with deterministic browser/ASR ports."""

import base64
import io
import wave
from unittest.mock import Mock

import pytest

from tests.test_meet_audio_profile import batch
from tests.test_meet_audio_profile_execution import ImmediatePool
from tests.test_meet_audio_receive import binding
from tests.test_meet_audio_segment import PROFILE, SPEECH
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_dialog_transport import assignment
from voice_runtime.backends.base import TranscriptionResult
from worker.meet_media import asr_pipeline, dialog_audio


@pytest.mark.parametrize("case", ["early", "maximum", "unavailable", "wrong_finish"])
def test_worker_stops_at_exact_boundary_before_asr_and_cannot_ignore_missing_port(monkeypatch, case):
    f, receipt, service, payload = runtime()
    f.context["audio_profile"] = PROFILE.projection()
    job = service.start(payload)["job"]
    wire = assignment() | {
        "audio_mode": "transcribe",
        "capabilities": ["audio.receive"],
        "audio_profile": PROFILE.projection(),
    }
    page, hub, backend = Mock(), Mock(), Mock()
    hub.call.return_value = {"reply": None}
    batches = [batch(start, 5, completed=start == 25) for start in range(0, 30, 5)]
    if case != "maximum":
        for value in batches:
            for chunk in value["chunks"]:
                if chunk["sequence"] <= 6:
                    chunk["pcmBase64"] = base64.b64encode(SPEECH).decode()
    samples = 48000 if case == "maximum" else 17600

    def transcribe(**kwargs):
        with wave.open(io.BytesIO(kwargs["content"]), "rb") as stream:
            assert stream.getnframes() == samples
        return TranscriptionResult(text="synthetic private", language="de", duration_ms=samples // 16)

    def evaluate(expression, *args):
        if "segmentProbe" in expression:
            return (
                None
                if case == "unavailable"
                else {
                    "schema": "ananta.meet-audio-segment-probe.v1",
                    "profile": "sample-boundary-v1",
                    "supported": True,
                }
            )
        if "audio.open" in expression:
            assert args == ([job["publication_id"], 3],)
            return {"subscriptionId": "a" * 32}
        if "audio.status" in expression:
            return True
        if "audio.poll" in expression:
            return batches.pop(0)
        if "audio.finish" in expression:
            assert args == (["a" * 32, samples],)
            assert backend.transcribe.call_count == 0
            return {
                "schema": "ananta.meet-audio-segment-finished.v1",
                "subscriptionId": "a" * 32,
                "endSample": samples - 1600 if case == "wrong_finish" else samples,
            }

    backend.transcribe.side_effect = transcribe
    page.evaluate.side_effect = evaluate
    monkeypatch.setattr(asr_pipeline, "MeetAsrPipeline", Mock(return_value=backend))
    monkeypatch.setattr(dialog_audio, "bind_subscription", lambda *_: binding())
    monkeypatch.setattr(dialog_audio, "ThreadPoolExecutor", lambda **_: ImmediatePool())
    if case == "unavailable":
        with pytest.raises(ValueError, match="segment_port_unavailable"):
            dialog_audio.DialogAudioPump(page, hub, wire, job)
        assert not any("audio.open" in call.args[0] for call in page.evaluate.call_args_list)
        return
    pump = dialog_audio.DialogAudioPump(page, hub, wire, job)
    try:
        if case == "wrong_finish":
            with pytest.raises(ValueError, match="segment_finish_invalid"):
                for _ in range(3):
                    pump.tick()
            backend.transcribe.assert_not_called()
            hub.call.assert_not_called()
        else:
            for _ in range(8 if case == "maximum" else 5):
                pump.tick()
            assert pump.closed and pump.pending is None
            backend.transcribe.assert_called_once()
            hub.call.assert_called_once()
            assert hub.call.call_args.kwargs["end_sample"] == samples
            assert pump.cursor.sequence * 1600 == samples
        assert not any("audio.reply" in call.args[0] for call in page.evaluate.call_args_list)
    finally:
        pump.close()
    assert pump.receiver._stream.recognizer is None
