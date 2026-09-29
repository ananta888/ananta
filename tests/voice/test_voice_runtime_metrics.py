from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from voice_runtime.app import create_app
from voice_runtime.backends.base import ChatResult, TranscriptionCandidate, TranscriptionResult
from voice_runtime.backends.mock import MockVoiceBackend
from voice_runtime.backends.router import RoutedVoiceBackend, _BackendEntry
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.errors import BackendUnavailableError
from voice_runtime.metrics import VoiceRuntimeMetrics
from voice_runtime.parallel import CandidateExecutionPolicy, ParallelCandidateExecutor

_TOKEN = "runtime-observability-test-token"
_AUTH = {"X-Ananta-Internal-Token": _TOKEN}


class _RecoveringBackend:
    def __init__(self, backend_id: str, *, fail_first: bool = False) -> None:
        self.backend_id = backend_id
        self.fail_first = fail_first
        self.calls = 0

    def name(self) -> str:
        return self.backend_id

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise BackendUnavailableError("redacted by the router")
        return TranscriptionResult(text=self.backend_id, raw_backend=self.backend_id)

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return ChatResult(text=self.backend_id)


def _client(*, enable_streaming: bool = False, store_audio: bool = False):
    app = create_app(
        VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            asr_backend="mock",
            primary_backend="mock",
            enable_streaming=enable_streaming,
            store_audio=store_audio,
            internal_service_token=_TOKEN,
        )
    )
    app.config.update(TESTING=True)
    return app.test_client()


def _scrape(client) -> str:
    response = client.get("/metrics", headers=_AUTH)
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    return str(response.get_data(as_text=True))


def test_metrics_endpoint_requires_the_internal_service_identity() -> None:
    client = _client(store_audio=True)

    unauthorized = client.get("/metrics")
    authorized = client.get("/metrics", headers=_AUTH)

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    payload = authorized.get_data(as_text=True)
    assert "voice_runtime_requests_total" in payload
    assert (
        'voice_runtime_privacy_state{store_audio_effective="false",store_audio_requested="true"} 1.0'
        in payload
    )


def test_batch_metrics_and_trace_are_correlated_without_content_labels() -> None:
    client = _client()
    request_id = "hub-voice-correlation-42"
    secret_filename = "customer-4711-secret-project.wav"
    secret_audio = b"private-audio-marker-should-never-be-exported"

    with patch("voice_runtime.app._log.info") as log_info:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"file": (BytesIO(secret_audio), secret_filename)},
            content_type="multipart/form-data",
            headers={**_AUTH, "X-Request-ID": request_id},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    template, *arguments = log_info.call_args.args
    log_text = template % tuple(arguments)
    assert f"request_id={request_id}" in log_text
    assert "store_audio_requested=false store_audio_effective=false" in log_text
    assert secret_filename not in log_text
    assert secret_audio.decode() not in log_text

    payload = _scrape(client)
    assert 'voice_runtime_requests_total{operation="transcribe",outcome="succeeded"} 1.0' in payload
    assert (
        'voice_runtime_backend_calls_total{backend="mock",operation="transcribe",outcome="succeeded"} 1.0'
        in payload
    )
    assert secret_filename not in payload
    assert secret_audio.decode() not in payload
    assert request_id not in payload


def test_stream_metrics_cover_replay_conflict_finalization_and_close() -> None:
    client = _client(enable_streaming=True)
    created = client.post(
        "/v1/audio/streams",
        json={"filename": "stream.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
        headers=_AUTH,
    )
    session_id = created.get_json()["session_id"]

    accepted = client.put(f"/v1/audio/streams/{session_id}/chunks/0", data=b"\x00\x00", headers=_AUTH)
    replayed = client.put(f"/v1/audio/streams/{session_id}/chunks/0", data=b"\x00\x00", headers=_AUTH)
    conflicted = client.put(f"/v1/audio/streams/{session_id}/chunks/0", data=b"\x01\x00", headers=_AUTH)
    finalized = client.post(f"/v1/audio/streams/{session_id}/finalize", headers=_AUTH)
    closed = client.delete(f"/v1/audio/streams/{session_id}", headers=_AUTH)

    assert accepted.status_code == 202
    assert replayed.get_json()["event"]["event_type"] == "chunk_replayed"
    assert conflicted.status_code == 409
    assert finalized.status_code == 200
    assert closed.status_code == 200

    payload = _scrape(client)
    expected_samples = (
        'voice_runtime_stream_events_total{event_type="created",outcome="succeeded"} 1.0',
        'voice_runtime_stream_events_total{event_type="chunk_accepted",outcome="succeeded"} 1.0',
        'voice_runtime_stream_events_total{event_type="chunk_replayed",outcome="succeeded"} 1.0',
        'voice_runtime_stream_events_total{event_type="error",outcome="conflict"} 1.0',
        'voice_runtime_stream_events_total{event_type="final",outcome="succeeded"} 1.0',
        'voice_runtime_stream_events_total{event_type="closed",outcome="succeeded"} 1.0',
    )
    assert all(sample in payload for sample in expected_samples)
    assert "voice_runtime_stream_chunk_bytes_count 1.0" in payload


def test_candidate_queue_backend_and_fusion_metrics_are_bounded() -> None:
    metrics = VoiceRuntimeMetrics()
    executor = ParallelCandidateExecutor(max_inflight_candidates=1, metrics=metrics)
    candidates = executor.execute(
        {"mock": MockVoiceBackend()},
        filename="sensitive-filename.wav",
        content=b"sensitive spoken content",
        language="de",
        policy=CandidateExecutionPolicy(max_parallel_backends=1),
    )
    candidate = TranscriptionCandidate(
        **{
            **candidates[0].__dict__,
            "real_time_factor": 0.5,
        }
    )
    result = TranscriptionResult(
        text="sensitive transcript",
        raw_backend="fusion",
        duration_ms=2_000,
        candidates=(candidate,),
        selected_candidate_id=candidate.candidate_id,
        fusion_strategy="deterministic_consensus",
        provenance_valid=True,
        rerun_backend="mock",
    )
    metrics.observe_transcription_result(result)
    metrics.observe_backend_call(
        operation="candidate",
        backend="private-user-defined-model-id",
        outcome="private-exception-message",
        duration_seconds=0.1,
    )

    payload = metrics.render().decode("utf-8")
    assert 'voice_runtime_queue_wait_seconds_count{outcome="acquired",surface="candidate_dispatch"} 1.0' in payload
    assert 'voice_runtime_candidates_total{backend="mock",outcome="succeeded"} 1.0' in payload
    assert (
        'voice_runtime_fusions_total{outcome="succeeded",strategy="deterministic_consensus"} 1.0' in payload
    )
    assert 'voice_runtime_backend_calls_total{backend="other",operation="candidate",outcome="other"} 1.0' in payload
    assert "sensitive transcript" not in payload
    assert "sensitive-filename" not in payload
    assert "private-user-defined-model-id" not in payload
    assert "private-exception-message" not in payload


def test_circuit_breaker_transitions_are_observable_without_backend_error_text() -> None:
    now = [0.0]
    metrics = VoiceRuntimeMetrics()
    primary = _RecoveringBackend("vosk", fail_first=True)
    fallback = _RecoveringBackend("mock")
    router = RoutedVoiceBackend(
        [_BackendEntry("vosk", primary), _BackendEntry("mock", fallback)],
        failure_threshold=1,
        cooldown_seconds=10,
        clock=lambda: now[0],
        metrics=metrics,
    )

    assert router.transcribe(filename="audio.wav", content=b"first").text == "mock"
    assert router.transcribe(filename="audio.wav", content=b"cooldown").text == "mock"
    now[0] = 11.0
    assert router.transcribe(filename="audio.wav", content=b"recovery").text == "vosk"

    payload = metrics.render().decode("utf-8")
    for event in ("opened", "open_skip", "half_open_probe", "recovered"):
        assert f'voice_runtime_circuit_breaker_events_total{{backend="vosk",event="{event}"}} 1.0' in payload
    assert "redacted by the router" not in payload
