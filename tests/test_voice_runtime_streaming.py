from __future__ import annotations

import threading
import time

import pytest

from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.execution_policy import HubVoiceConfiguration
from voice_runtime.streaming import (
    StreamProtocolError,
    StreamSessionManager,
    StreamState,
    buffered_pipeline_recognizer_factory,
    buffered_recognizer_factory,
    container_safe_recognizer_factory,
)


class _Backend:
    def name(self):
        return "test"

    def transcribe(self, *, filename, content, language=None):
        return TranscriptionResult(
            text=content.decode(),
            language=language,
            duration_ms=max(1, len(content) * 2),
            raw_backend="test",
        )

    def audio_chat(self, **kwargs):
        raise NotImplementedError

    def list_models(self):
        return []

    def context_capabilities(self):
        return frozenset()


class _DecodedAudio:
    def __init__(self, duration_ms: int) -> None:
        self.duration_ms = duration_ms
        self.sample_rate_hz = 1_000
        self.frame_count = duration_ms


class _SyntheticContainerDecoder:
    def __init__(self, duration_ms: int | None = None) -> None:
        self._duration_ms = duration_ms
        self.calls = 0

    def decode(self, *, filename: str, payload: bytes):
        del filename
        self.calls += 1
        return _DecodedAudio(self._duration_ms if self._duration_ms is not None else max(1, len(payload) * 2))


def _container_decoder_factory(_max_audio_seconds: float, _max_encoded_bytes: int):
    return _SyntheticContainerDecoder()


def _manager(**kwargs):
    kwargs.setdefault("container_audio_decoder_factory", _container_decoder_factory)
    return StreamSessionManager(buffered_recognizer_factory(_Backend()), **kwargs)


class _TrackingRecognizer:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.close_count = 0

    def accept(self, content: bytes):
        self.buffer.extend(content)
        return None

    def finish(self):
        return TranscriptionResult(
            text=self.buffer.decode(),
            duration_ms=max(1, len(self.buffer) * 2),
            raw_backend="tracking",
        )

    def close(self):
        self.close_count += 1
        for index in range(len(self.buffer)):
            self.buffer[index] = 0
        self.buffer.clear()


def test_stream_orders_chunks_and_finalize_is_idempotent():
    session = _manager().create(filename="sample.webm", language="de", media_type="audio/webm")

    first = session.push(chunk_sequence=0, content=b"Hallo ")
    replay = session.push(chunk_sequence=0, content=b"Hallo ")
    session.push(chunk_sequence=1, content=b"Welt")
    final = session.finalize()
    final_replay = session.finalize()

    assert first.event_type == "chunk_accepted"
    assert replay.event_type == "chunk_replayed"
    assert final.payload["result"]["text"] == "Hallo Welt"
    assert final_replay.event_type == "final_replayed"


def test_finalize_releases_audio_state_but_preserves_result_and_replay_contract():
    recognizer = _TrackingRecognizer()
    session = StreamSessionManager(
        lambda *_args: recognizer,
        container_audio_decoder_factory=_container_decoder_factory,
    ).create(
        filename="sample.webm",
        language="de",
        media_type="audio/webm",
    )
    session.push(chunk_sequence=0, content=b"private audio")

    final = session.finalize()
    snapshot = session.snapshot()
    replay = session.finalize()

    assert final.payload["result"]["text"] == "private audio"
    assert snapshot["result"]["text"] == "private audio"
    assert replay.event_type == "final_replayed"
    assert session.recognizer is None
    assert session.chunk_digests == {}
    assert recognizer.buffer == bytearray()
    assert recognizer.close_count == 1


def test_stream_rejects_gap_conflict_and_byte_overflow():
    session = _manager(max_chunk_bytes=4, max_total_bytes=6).create(
        filename="sample.pcm", language=None, media_type="audio/pcm;rate=16000;channels=1"
    )

    with pytest.raises(StreamProtocolError, match="expected chunk"):
        session.push(chunk_sequence=1, content=b"a")
    session.push(chunk_sequence=0, content=b"abcd")
    with pytest.raises(StreamProtocolError, match="differs"):
        session.push(chunk_sequence=0, content=b"abce")
    with pytest.raises(StreamProtocolError, match="byte budget"):
        session.push(chunk_sequence=1, content=b"efg")


def test_pcm_stream_enforces_forwarded_audio_duration_budget() -> None:
    session = _manager(
        max_chunk_bytes=64,
        max_total_bytes=1_024,
        default_max_audio_seconds=10,
    ).create(
        filename="sample.pcm",
        language=None,
        media_type="audio/pcm;rate=16000;channels=1",
        max_audio_seconds=0.001,
    )

    session.push(chunk_sequence=0, content=b"a" * 32)
    with pytest.raises(StreamProtocolError) as exceeded:
        session.push(chunk_sequence=1, content=b"b")

    assert exceeded.value.code == "stream.total_too_large"
    assert session.max_total_bytes == 32


def test_container_stream_preserves_forwarded_duration_budget_and_rejects_decoded_overrun() -> None:
    class _BackendSpy(_TrackingRecognizer):
        accept_calls = 0
        finish_calls = 0

        def accept(self, content: bytes):
            self.accept_calls += 1
            return super().accept(content)

        def finish(self):
            self.finish_calls += 1
            return super().finish()

    recognizer = _BackendSpy()
    decoder = _SyntheticContainerDecoder(duration_ms=1_001)
    decoder_budgets: list[tuple[float, int]] = []

    def decoder_factory(max_audio_seconds: float, max_encoded_bytes: int):
        decoder_budgets.append((max_audio_seconds, max_encoded_bytes))
        return decoder

    session = StreamSessionManager(
        lambda *_args: recognizer,
        default_max_audio_seconds=30,
        container_audio_decoder_factory=decoder_factory,
    ).create(
        filename="sample.webm",
        language=None,
        media_type="audio/webm",
        max_audio_seconds=1,
    )
    session.push(chunk_sequence=0, content=b"compressed audio")
    assert recognizer.accept_calls == 0

    with pytest.raises(StreamProtocolError) as exceeded:
        session.finalize()

    assert session.max_audio_seconds == 1
    assert exceeded.value.code == "stream.audio_duration_exceeded"
    assert exceeded.value.status_code == 413
    assert session.state is StreamState.FAILED
    assert session.result is None
    assert session.recognizer is None
    assert session.chunk_digests == {}
    assert decoder.calls == 1
    assert decoder_budgets == [(1, session.max_total_bytes)]
    assert recognizer.accept_calls == 0
    assert recognizer.finish_calls == 0
    assert recognizer.close_count == 1


def test_container_stream_uses_trusted_preflight_duration_when_backend_omits_it() -> None:
    class _MissingDurationRecognizer(_TrackingRecognizer):
        def finish(self):
            return TranscriptionResult(text="audio", raw_backend="tracking")

    session = StreamSessionManager(
        lambda *_args: _MissingDurationRecognizer(),
        container_audio_decoder_factory=_container_decoder_factory,
    ).create(
        filename="sample.wav",
        language=None,
        media_type="audio/wav",
        max_audio_seconds=1,
    )
    session.push(chunk_sequence=0, content=b"container")

    final = session.finalize()

    assert final.payload["result"]["duration_ms"] == len(b"container") * 2
    assert session.state is StreamState.FINAL


def test_compressed_container_preflight_blocks_pipeline_before_any_inference() -> None:
    class _PipelineSpy:
        calls = 0

        def transcribe(self, **_kwargs):
            self.calls += 1
            return TranscriptionResult(text="must not execute", duration_ms=1)

    pipeline = _PipelineSpy()
    decoder = _SyntheticContainerDecoder(duration_ms=2_000)
    manager = StreamSessionManager(
        buffered_recognizer_factory(_Backend()),
        policy_recognizer_factory=buffered_pipeline_recognizer_factory(pipeline),
        default_max_audio_seconds=30,
        container_audio_decoder_factory=lambda *_args: decoder,
    )
    session = manager.create(
        filename="compressed.webm",
        language=None,
        media_type="audio/webm",
        max_audio_seconds=1,
        recognition_context=VoiceRecognitionContext(
            configuration=HubVoiceConfiguration(candidate_deadline_sec=10),
        ),
    )
    session.push(chunk_sequence=0, content=b"opaque compressed container")

    with pytest.raises(StreamProtocolError) as exceeded:
        session.finalize()

    assert exceeded.value.code == "stream.audio_duration_exceeded"
    assert pipeline.calls == 0
    assert decoder.calls == 1
    assert session.state is StreamState.FAILED
    assert session.recognizer is None
    assert session.chunk_digests == {}


def test_compressed_container_never_uses_incremental_backend_adapter() -> None:
    class _BackendSpy(_Backend):
        calls = 0

        def transcribe(self, *, filename, content, language=None):
            self.calls += 1
            return super().transcribe(filename=filename, content=content, language=language)

    backend = _BackendSpy()
    incremental_factory_calls = 0

    def incremental_factory(*_args):
        nonlocal incremental_factory_calls
        incremental_factory_calls += 1
        return _TrackingRecognizer()

    decoder = _SyntheticContainerDecoder(duration_ms=2_000)
    manager = StreamSessionManager(
        container_safe_recognizer_factory(backend, incremental_factory),
        container_audio_decoder_factory=lambda *_args: decoder,
        default_max_audio_seconds=30,
    )
    session = manager.create(
        filename="compressed.webm",
        language=None,
        media_type="audio/webm",
        max_audio_seconds=1,
    )
    session.push(chunk_sequence=0, content=b"opaque compressed container")

    with pytest.raises(StreamProtocolError) as exceeded:
        session.finalize()

    assert exceeded.value.code == "stream.audio_duration_exceeded"
    assert incremental_factory_calls == 0
    assert backend.calls == 0


def test_stream_bounds_chunk_count_and_replay_digest_history():
    session = _manager(max_chunks_per_session=2, replay_window_chunks=1).create(
        filename="sample.pcm",
        language=None,
        media_type="audio/pcm;rate=16000;channels=1",
    )

    session.push(chunk_sequence=0, content=b"a")
    session.push(chunk_sequence=1, content=b"b")

    assert session.chunk_digests == {1: session.chunk_digests[1]}
    with pytest.raises(StreamProtocolError) as expired:
        session.push(chunk_sequence=0, content=b"a")
    assert expired.value.code == "stream.replay_window_expired"
    with pytest.raises(StreamProtocolError) as exhausted:
        session.push(chunk_sequence=2, content=b"c")
    assert exhausted.value.code == "stream.chunk_limit_exceeded"


def test_stream_capacity_cleanup_and_no_access_after_delete():
    manager = _manager(max_sessions=1)
    session = manager.create(filename="one.webm", language=None, media_type="audio/webm")
    with pytest.raises(StreamProtocolError, match="capacity"):
        manager.create(filename="two.webm", language=None, media_type="audio/webm")

    assert manager.delete(session.session_id)
    with pytest.raises(StreamProtocolError, match="not found"):
        manager.get(session.session_id)


def test_manager_atomically_reserves_requested_session_id_and_rejects_duplicate() -> None:
    manager = _manager()
    requested_session_id = f"vs_{'A' * 32}"
    barrier = threading.Barrier(2)
    sessions = []
    errors: list[StreamProtocolError] = []

    def create() -> None:
        barrier.wait(timeout=1)
        try:
            sessions.append(
                manager.create(
                    filename="sample.webm",
                    language=None,
                    media_type="audio/webm",
                    requested_session_id=requested_session_id,
                )
            )
        except StreamProtocolError as exc:
            errors.append(exc)

    workers = [threading.Thread(target=create) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)

    assert [session.session_id for session in sessions] == [requested_session_id]
    assert [error.code for error in errors] == ["stream.session_id_conflict"]
    assert errors[0].status_code == 409
    assert manager.get(requested_session_id) is sessions[0]


def test_expired_session_get_releases_audio_without_creating_another_session():
    recognizer = _TrackingRecognizer()
    manager = StreamSessionManager(lambda *_args: recognizer)
    session = manager.create(
        filename="expired.webm",
        language=None,
        media_type="audio/webm",
    )
    session.push(chunk_sequence=0, content=b"sensitive")
    session.deadline_monotonic = time.monotonic() - 1

    with pytest.raises(StreamProtocolError) as error:
        manager.get(session.session_id)

    assert error.value.code == "stream.not_found"
    assert session.state is StreamState.CLOSED
    assert session.recognizer is None
    assert session.chunk_digests == {}
    assert recognizer.buffer == bytearray()
    assert recognizer.close_count == 1


def test_expired_session_snapshot_releases_audio_for_existing_reader():
    recognizer = _TrackingRecognizer()
    session = StreamSessionManager(
        lambda *_args: recognizer,
        container_audio_decoder_factory=_container_decoder_factory,
    ).create(
        filename="expired.webm",
        language=None,
        media_type="audio/webm",
    )
    session.push(chunk_sequence=0, content=b"sensitive")
    session.deadline_monotonic = time.monotonic() - 1

    with pytest.raises(StreamProtocolError) as error:
        session.snapshot()

    assert error.value.code == "stream.deadline_exceeded"
    assert session.state is StreamState.FAILED
    assert session.recognizer is None
    assert recognizer.buffer == bytearray()
    assert recognizer.close_count == 1


def test_manager_get_sweeps_other_expired_sessions_before_reading_live_session():
    recognizers: list[_TrackingRecognizer] = []

    def create_recognizer(*_args):
        recognizer = _TrackingRecognizer()
        recognizers.append(recognizer)
        return recognizer

    manager = StreamSessionManager(create_recognizer)
    expired = manager.create(filename="expired.webm", language=None, media_type="audio/webm")
    live = manager.create(filename="live.webm", language=None, media_type="audio/webm")
    expired.push(chunk_sequence=0, content=b"sensitive")
    expired.deadline_monotonic = time.monotonic() - 1

    assert manager.get(live.session_id) is live
    with pytest.raises(StreamProtocolError) as error:
        manager.get(expired.session_id)

    assert error.value.code == "stream.not_found"
    assert expired.state is StreamState.CLOSED
    assert expired.recognizer is None
    assert recognizers[0].buffer == bytearray()
    assert recognizers[0].close_count == 1


def test_close_is_idempotent_after_finalize():
    recognizer = _TrackingRecognizer()
    session = StreamSessionManager(
        lambda *_args: recognizer,
        container_audio_decoder_factory=_container_decoder_factory,
    ).create(
        filename="sample.webm",
        language=None,
        media_type="audio/webm",
    )
    session.push(chunk_sequence=0, content=b"audio")
    session.finalize()

    session.close()
    session.close()

    assert session.state is StreamState.CLOSED
    assert session.recognizer is None
    assert session.events == []
    assert session.result is None
    assert recognizer.close_count == 1


def test_stream_backpressure_rejects_overlapping_chunk_processing():
    entered = threading.Event()
    release = threading.Event()

    class _SlowRecognizer:
        def accept(self, content):
            entered.set()
            release.wait(timeout=1)
            return None

        def finish(self):
            return TranscriptionResult(text="done")

        def close(self):
            return None

    manager = StreamSessionManager(lambda *_args: _SlowRecognizer())
    session = manager.create(filename="audio.pcm", language=None, media_type="audio/pcm;rate=16000;channels=1")
    worker = threading.Thread(target=lambda: session.push(chunk_sequence=0, content=b"a"))
    worker.start()
    assert entered.wait(timeout=1)
    with pytest.raises(StreamProtocolError, match="still being processed"):
        session.push(chunk_sequence=1, content=b"b")
    release.set()
    worker.join(timeout=1)


def test_stream_deadline_includes_recognizer_model_loading(monkeypatch):
    closed = threading.Event()

    class _Recognizer:
        def accept(self, _content):
            return None

        def finish(self):
            return TranscriptionResult(text="late")

        def close(self):
            closed.set()

    monotonic_values = iter((10.0, 12.0))
    monkeypatch.setattr("voice_runtime.streaming.monotonic", lambda: next(monotonic_values))
    manager = StreamSessionManager(lambda *_args: _Recognizer(), default_deadline_seconds=1)

    with pytest.raises(StreamProtocolError) as error:
        manager.create(
            filename="audio.webm",
            language=None,
            media_type="audio/webm",
            deadline_seconds=1,
        )

    assert error.value.code == "stream.deadline_exceeded"
    assert closed.is_set()


def test_stream_manager_accepts_hub_total_deadline_beyond_candidate_timeout():
    manager = _manager(default_deadline_seconds=300)

    session = manager.create(
        filename="audio.pcm",
        language=None,
        media_type="audio/pcm;rate=16000;channels=1",
        deadline_seconds=245,
        max_audio_seconds=120,
    )

    assert 244 <= session.deadline_monotonic - time.monotonic() <= 245


def test_stream_manager_caps_hub_deadline_at_runtime_stream_timeout():
    manager = _manager(default_deadline_seconds=245)

    session = manager.create(
        filename="audio.pcm",
        language=None,
        media_type="audio/pcm;rate=16000;channels=1",
        deadline_seconds=300,
        max_audio_seconds=120,
    )

    assert 244 <= session.deadline_monotonic - time.monotonic() <= 245


def test_stream_finalization_narrows_pipeline_to_remaining_session_deadline():
    class _Pipeline:
        observed_deadline = 0.0

        def transcribe(self, *, filename, content, language=None, context=None):
            assert context is not None and context.configuration is not None
            self.observed_deadline = context.configuration.candidate_deadline_sec
            return TranscriptionResult(text=content.decode(), duration_ms=10, raw_backend="test")

    pipeline = _Pipeline()
    manager = StreamSessionManager(
        buffered_recognizer_factory(_Backend()),
        policy_recognizer_factory=buffered_pipeline_recognizer_factory(pipeline),
        container_audio_decoder_factory=_container_decoder_factory,
    )
    context = VoiceRecognitionContext(
        configuration=HubVoiceConfiguration(candidate_deadline_sec=120.0),
    )
    session = manager.create(
        filename="audio.webm",
        language=None,
        media_type="audio/webm",
        recognition_context=context,
    )
    session.push(chunk_sequence=0, content=b"audio")
    session.deadline_monotonic = time.monotonic() + 0.2
    session.finalize()

    assert 0 < pipeline.observed_deadline <= 0.2
