"""Actual bounded PCM receiver/pump with explicit local-ASR and browser doubles."""

import base64
import io
import wave
from concurrent.futures import Future
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_audio_profile import AudioReceiveProfile
from tests.test_meet_audio_profile import batch
from tests.test_meet_audio_receive import binding, wav
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_dialog_transport import assignment
from voice_runtime.backends.base import TranscriptionResult
from worker.meet_media import asr_child, asr_pipeline, dialog_audio

pytestmark = pytest.mark.timeout(15)
PROFILE = AudioReceiveProfile(language="en", vad="off", segment_seconds=2)


@pytest.mark.parametrize("vad", ["off", "local-vad-v1"])
def test_pinned_native_child_receives_only_bounded_explicit_local_profile(monkeypatch, vad):
    profile = AudioReceiveProfile(language="en", vad=vad, segment_seconds=2)
    verify, backend = Mock(), Mock()
    backend.return_value.transcribe.return_value = TranscriptionResult(text="synthetic", language="en", duration_ms=10)
    monkeypatch.setattr(asr_child, "verify_model", verify)
    monkeypatch.setattr(asr_child, "FasterWhisperBackend", backend)
    result = asr_child.transcribe(
        {"wav": base64.b64encode(wav()).decode(), "language": "en", "audio_profile": profile.projection()}
    )
    options = backend.call_args.kwargs
    assert options["model_path"] == "/models/faster-whisper-small"
    assert options["device"] == "cuda" and options["compute_type"] == "float16"
    assert options["allow_download"] is False and options["vad_filter"] is (vad == "local-vad-v1")
    assert options["decoder"]._limits.max_duration_ms == 2000
    assert result["language"] == "en" and result["device"] == "cuda"
    verify.assert_called_once()


class ImmediatePool:
    """Deterministic scheduling seam; preserves result/exception Future semantics."""

    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as error:
            future.set_exception(error)
        return future

    def shutdown(self, **kwargs):
        assert kwargs == {"wait": False, "cancel_futures": False}


def pump_fixture(monkeypatch, reply=None):
    f, receipt, service, payload = runtime()
    f.context["audio_profile"] = PROFILE.projection()
    job = service.start(payload)["job"]
    delegated = assignment() | {
        "audio_mode": "transcribe",
        "capabilities": ["audio.receive"],
        "audio_profile": PROFILE.projection(),
    }
    backend, pipeline = Mock(), Mock()

    def transcribe(**kwargs):
        assert kwargs["language"] == "en"
        with wave.open(io.BytesIO(kwargs["content"]), "rb") as stream:
            assert stream.getnframes() == 32000 and stream.getframerate() == 16000
        return TranscriptionResult(text="synthetic ephemeral", language="en", duration_ms=2000)

    backend.transcribe.side_effect = transcribe
    pipeline.return_value = backend
    monkeypatch.setattr(asr_pipeline, "MeetAsrPipeline", pipeline)
    monkeypatch.setattr(dialog_audio, "bind_subscription", lambda *_: binding())
    monkeypatch.setattr(dialog_audio, "ThreadPoolExecutor", lambda **_: ImmediatePool())
    page, hub = Mock(), Mock()
    batches = [batch(start, 5, completed=start == 15) for start in range(0, 20, 5)]

    def evaluate(expression, *args):
        if "audio.open" in expression:
            assert args == ([job["publication_id"], 2],)
            return {"subscriptionId": "a" * 32}
        if "audio.status" in expression:
            return True
        if "audio.poll" in expression:
            return batches.pop(0)

    page.evaluate.side_effect = evaluate
    hub.call.return_value = {"reply": reply}
    pump = dialog_audio.DialogAudioPump(page, hub, delegated, job)
    return pump, page, hub, backend, pipeline


def test_two_second_receiver_transcribes_exact_samples_once_then_clears_context(monkeypatch):
    pump, page, hub, backend, pipeline = pump_fixture(monkeypatch)
    try:
        for _ in range(6):
            pump.tick()
        assert pump.closed and pump.pending is None
        assert pump.receiver._stream.result is None and pump.receiver._stream.recognizer is None
        assert pump.receiver._next_sample is None
        assert pipeline.call_args.kwargs["audio_profile"] == PROFILE.projection()
        backend.transcribe.assert_called_once()
        hub.call.assert_called_once_with(
            "transcript",
            meet_session_id=pump.job["meet_session_id"],
            audio_task_id=pump.job["task_id"],
            audio_lease_id=pump.job["lease_id"],
            end_sample=32000,
            language="en",
            text="synthetic ephemeral",
        )
        assert not any("audio.reply" in call.args[0] for call in page.evaluate.call_args_list)
    finally:
        pump.close()


def test_cancelled_segment_wipes_audio_without_asr_or_hub_transcript(monkeypatch):
    pump, page, hub, backend, pipeline = pump_fixture(monkeypatch)
    pump.tick()
    pump.close()
    pump.tick()
    backend.transcribe.assert_not_called()
    hub.call.assert_not_called()
    assert pump.receiver._stream.recognizer is None and pump.pending is None
    assert page.evaluate.call_args.args[0] == "window.anantaMachine.audio.close()"


def test_transcribe_only_worker_cannot_publish_unexpected_hub_reply(monkeypatch):
    pump, page, hub, backend, pipeline = pump_fixture(monkeypatch, reply={"text": "must not publish"})
    try:
        for _ in range(5):
            pump.tick()
        with pytest.raises(ValueError, match="reply_policy_denied"):
            pump.tick()
        assert not any("audio.reply" in call.args[0] for call in page.evaluate.call_args_list)
    finally:
        pump.close()
