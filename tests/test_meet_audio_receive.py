"""Headless publication binding, sample timelines, cleanup and local ASR limits."""

import io
import json
import subprocess
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from voice_runtime.streaming import StreamState
from worker.meet_media.asr_model import REVISION
from worker.meet_media.asr_pipeline import MeetAsrPipeline
from worker.meet_media.audio_receive import MeetAudioReceiver, ReceiveBinding

pytestmark = pytest.mark.timeout(30)
PCM = b"\x01\x02" * 160


def binding(**changes):
    value = ReceiveBinding(
        tenant_id="tenant",
        project_id="project",
        task_id="task",
        lease_id="lease",
        runtime_id="runtime",
        session_id="session",
        generation=1,
        room_id="room-111111111111111111",
        membership_epoch=1,
        peer_id="peer",
        own_peer_id="self",
        publication_id="microphone",
        publication_epoch=1,
        source="microphone",
    )
    return replace(value, **changes)


def pipeline():
    backend = Mock()

    def transcribe(**kwargs):
        with wave.open(io.BytesIO(kwargs["content"]), "rb") as source:
            assert source.getframerate() == 16_000 and source.getnchannels() == 1 and source.getsampwidth() == 2
            duration = source.getnframes() * 1000 // 16_000
        return TranscriptionResult(text="synthetic transcript", language="de", duration_ms=duration)

    backend.transcribe.side_effect = transcribe
    return backend


def test_source_and_sample_timeline_survive_asr_without_raw_hub_media():
    lease, backend = Mock(), pipeline()
    source = MeetAudioReceiver(binding(), lease, backend)
    source.push(binding(), start_sample=1600, pcm=PCM)
    source.push(binding(), start_sample=1760, pcm=PCM)
    transcript = source.finish()
    assert transcript.binding == binding()
    assert transcript.start_sample == 1600 and transcript.end_sample == 1920
    assert transcript.text == "synthetic transcript" and transcript.text not in repr(transcript)
    assert source._stream.state == StreamState.CLOSED and not source._stream.events
    assert source._stream.result is None
    backend.cancel.assert_called_once()
    backend.transcribe.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"own_peer_id": "peer"},
        {"source": "generated_audio"},
        {"source": "camera"},
        {"generation": True},
        {"publication_epoch": 0},
        {"peer_id": "../peer"},
        {"room_id": "other"},
        {"membership_epoch": 2**53},
    ],
)
def test_self_input_and_invalid_binding_are_rejected(change):
    with pytest.raises(ValueError):
        binding(**change)


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": "other"},
        {"generation": 2},
        {"publication_id": "other"},
        {"publication_epoch": 2},
        {"lease_id": "other"},
        {"runtime_id": "other"},
        {"peer_id": "other"},
        {"source": "screen_audio"},
        {"tenant_id": "other"},
        {"project_id": "other"},
        {"room_id": "room-222222222222222222"},
        {"membership_epoch": 2},
    ],
)
def test_changed_binding_cannot_reuse_any_prior_audio(change):
    backend = pipeline()
    source = MeetAudioReceiver(binding(), Mock(), backend)
    recognizer = source._stream.recognizer
    source.push(binding(), start_sample=0, pcm=PCM)
    with pytest.raises(ValueError, match="binding_stale"):
        source.push(binding(**change), start_sample=160, pcm=PCM)
    assert not recognizer._buffer and source._stream.state == StreamState.CLOSED
    backend.transcribe.assert_not_called()


@pytest.mark.parametrize("start_sample", [0, 159, 161, -1, True, 2**53])
def test_duplicate_gap_and_invalid_sample_offsets_stop_source(start_sample):
    source = MeetAudioReceiver(binding(), Mock(), pipeline())
    source.push(binding(), start_sample=0, pcm=PCM)
    with pytest.raises(ValueError):
        source.push(binding(), start_sample=start_sample, pcm=PCM)
    assert source._stream.state == StreamState.CLOSED


@pytest.mark.parametrize("pcm", [b"", b"a", b"a" * 319, b"a" * 3520, "not bytes"])
def test_canonical_pcm_chunk_shape_is_not_negotiated_by_input(pcm):
    source = MeetAudioReceiver(binding(), Mock(), pipeline())
    with pytest.raises(ValueError, match="chunk_invalid"):
        source.push(binding(), start_sample=0, pcm=pcm)


def test_revocation_wipes_buffer_and_cannot_flush_transcript():
    lease, backend = Mock(), pipeline()
    source = MeetAudioReceiver(binding(), lease, backend)
    recognizer = source._stream.recognizer
    source.push(binding(), start_sample=0, pcm=PCM)
    lease.require.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        source.finish()
    assert not recognizer._buffer and not source._stream.events
    backend.transcribe.assert_not_called()


def test_buffer_budget_and_close_are_bounded():
    source = MeetAudioReceiver(binding(), Mock(), pipeline(), max_audio_seconds=1)
    for chunk in range(10):
        source.push(binding(), start_sample=chunk * 1600, pcm=PCM * 10)
    assert source._stream.total_bytes == 32000 and len(source._stream.events) <= 2
    with pytest.raises(Exception):
        source.push(binding(), start_sample=16000, pcm=PCM)
    source.close()
    assert source._stream.recognizer is None and not source._stream.chunk_digests


def test_concurrent_duplicate_packets_cannot_race_sample_accounting():
    source = MeetAudioReceiver(binding(), Mock(), pipeline())
    barrier = threading.Barrier(2)

    def send():
        barrier.wait(timeout=3)
        try:
            source.push(binding(), start_sample=0, pcm=PCM)
            return "accepted"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(send) for _ in range(2)]
        assert sorted(future.result(timeout=3) for future in pending) == ["accepted", "rejected"]
    assert source._stream.state == StreamState.CLOSED
    assert source._next_sample is None and source._stream.recognizer is None


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(PCM)
    return output.getvalue()


def response(**changes):
    return {
        "schema": "ananta.meet-asr-result.v1",
        "text": "Hallo",
        "language": "de",
        "duration_ms": 10,
        "model_revision": REVISION,
        "device": "cuda",
    } | changes


def test_existing_voice_backend_can_import_without_loading_web_application():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import voice_runtime.backends.faster_whisper; assert 'voice_runtime.app' not in sys.modules",
        ],
        check=True,
        timeout=10,
    )


def test_asr_subprocess_is_bounded_and_never_gets_context_or_filename():
    runner, lease = Mock(), Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(response()).encode())
    adapter = MeetAsrPipeline(binding(), lease, deadline_monotonic=time.monotonic() + 30, runner=runner)
    result = adapter.transcribe(
        filename="/private/profile", content=wav(), language="de", context={"private": "secret"}
    )
    assert result.text == "Hallo"
    call = runner.run.call_args.kwargs
    assert call["timeout_seconds"] <= 20 and call["max_stdout_bytes"] == 12000
    assert set(json.loads(call["input_payload"])) == {"wav", "language"}
    assert b"private" not in call["input_payload"] and b"secret" not in call["input_payload"]
    adapter.cancel()
    with pytest.raises(ValueError, match="cancelled"):
        call["cancellation_check"]()


@pytest.mark.parametrize(
    "change",
    [
        {"device": "cpu"},
        {"model_revision": "main"},
        {"duration_ms": True},
        {"duration_ms": 10001},
        {"language": "en"},
        {"text": "x" * 2001},
        {"extra": True},
    ],
)
def test_model_result_cannot_change_profile_or_budget(change):
    runner = Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(response(**change)).encode())
    adapter = MeetAsrPipeline(binding(), Mock(), deadline_monotonic=time.monotonic() + 30, runner=runner)
    with pytest.raises(ValueError, match="failed_or_revoked"):
        adapter.transcribe(filename="test", content=wav(), language="de")


def test_cancel_terminates_active_transcription_before_waiting_for_stream_lock():
    running = threading.Event()
    runner = Mock()

    def run(*_, **kwargs):
        running.set()
        while True:
            kwargs["cancellation_check"]()
            time.sleep(0.01)

    runner.run.side_effect = run
    backend = MeetAsrPipeline(binding(), Mock(), deadline_monotonic=time.monotonic() + 10, runner=runner)
    source = MeetAudioReceiver(binding(), Mock(), backend)
    source.push(binding(), start_sample=0, pcm=PCM)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(source.finish)
        assert running.wait(timeout=3)
        stopped = executor.submit(source.close)
        stopped.result(timeout=3)
        with pytest.raises(ValueError, match="failed_or_revoked"):
            pending.result(timeout=3)
    assert source._stream.state == StreamState.CLOSED


@pytest.mark.parametrize("paths", [("relative",), ("/a:/b",), ("/a\x00",), ("/a",) * 17])
def test_gpu_library_configuration_is_operator_bound_and_closed(paths):
    with pytest.raises(ValueError, match="library_paths_invalid"):
        BoundedSubprocessRunner(library_paths=paths)


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, 0, "later"])
def test_asr_deadline_must_be_a_finite_bounded_assignment_value(deadline):
    with pytest.raises(ValueError, match="deadline_invalid"):
        MeetAsrPipeline(binding(), Mock(), deadline_monotonic=deadline)
