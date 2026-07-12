from __future__ import annotations

import threading

import pytest

from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.execution_policy import HubVoiceConfiguration
from voice_runtime.streaming import (
    BufferedPipelineRecognizer,
    StreamProtocolError,
    StreamSessionManager,
    policy_streaming_recognizer_factory,
)
from voice_runtime.streaming_fusion import (
    IncrementalFusionRecognizer,
    IncrementalHypothesis,
    StreamingModel,
)


class _ScriptedRecognizer:
    def __init__(self, updates, final_text: str) -> None:
        self._updates = list(updates)
        self._final_text = final_text
        self.closed = False

    def accept(self, _content: bytes):
        update = self._updates.pop(0)
        if isinstance(update, Exception):
            raise update
        return update

    def finish(self) -> TranscriptionResult:
        return TranscriptionResult(text=self._final_text, raw_backend="scripted", confidence=0.8)

    def close(self) -> None:
        self.closed = True


def _recognizer_pair():
    first = _ScriptedRecognizer(
        [
            IncrementalHypothesis("Hallo liebe Welt", "Hallo", 100),
            IncrementalHypothesis("Hallo liebe Welt heute", "Hallo liebe", 200),
        ],
        "Hallo liebe Welt heute",
    )
    second = _ScriptedRecognizer(
        [
            IncrementalHypothesis("Hallo lieber Welt", "Hallo", 100),
            IncrementalHypothesis("Hallo liebe Welt heute", "Hallo liebe", 200),
        ],
        "Hallo liebe Welt heute",
    )
    return first, second


def _fusion_factory(created: list[tuple[_ScriptedRecognizer, _ScriptedRecognizer]]):
    def create(*_args):
        first, second = _recognizer_pair()
        created.append((first, second))
        return IncrementalFusionRecognizer(
            (
                StreamingModel("alpha", first),
                StreamingModel("beta", second),
            )
        )

    return create


def test_incremental_fusion_partial_disagreement_final_cancel_and_deterministic_trace() -> None:
    created: list[tuple[_ScriptedRecognizer, _ScriptedRecognizer]] = []
    manager = StreamSessionManager(_fusion_factory(created))

    first_session = manager.create(
        filename="speech.pcm",
        language="de",
        media_type="audio/pcm;rate=16000;channels=1",
    )
    first = first_session.push(chunk_sequence=0, content=b"\x00\x00")
    second = first_session.push(chunk_sequence=1, content=b"\x01\x00")
    final = first_session.finalize()

    assert first.event_type == "partial_fusion"
    assert second.event_type == "partial_fusion"
    assert first.sequence < second.sequence < final.sequence
    assert [first.payload["fusion_version"], second.payload["fusion_version"]] == [1, 2]
    assert first.payload["disagreement"] is True
    assert second.payload["disagreement"] is False
    assert first.payload["stable_text"] == "Hallo"
    assert second.payload["stable_text"] == "Hallo liebe"
    assert [first.payload["finalized_until_ms"], second.payload["finalized_until_ms"]] == [100, 200]
    candidate_versions = [
        [candidate["candidate_version"] for candidate in event.payload["candidates"]] for event in (first, second)
    ]
    assert candidate_versions == [[1, 1], [2, 2]]
    result = final.payload["result"]
    assert result["text"] == "Hallo liebe Welt heute"
    assert result["fusion_strategy"] == "incremental_deterministic_consensus"
    trace = result["decision_trace"]["streaming_fusion"]
    assert trace["policy_owner"] == "hub"
    assert trace["partial_trace_hashes"] == [
        first.payload["trace_hash"],
        second.payload["trace_hash"],
    ]

    replay_session = manager.create(
        filename="speech.pcm",
        language="de",
        media_type="audio/pcm;rate=16000;channels=1",
    )
    replay_events = [
        replay_session.push(chunk_sequence=0, content=b"\x00\x00"),
        replay_session.push(chunk_sequence=1, content=b"\x01\x00"),
    ]
    assert [event.payload["trace_hash"] for event in replay_events] == trace["partial_trace_hashes"]

    cancelled = manager.create(
        filename="speech.pcm",
        language="de",
        media_type="audio/pcm;rate=16000;channels=1",
    )
    assert manager.delete(cancelled.session_id) is True
    assert all(recognizer.closed for recognizer in created[-1])
    with pytest.raises(StreamProtocolError, match="not found"):
        manager.get(cancelled.session_id)


def test_incremental_fusion_isolates_model_failure_and_preserves_finalized_prefix() -> None:
    healthy = _ScriptedRecognizer(
        [
            IncrementalHypothesis("Hallo Welt", "Hallo", 100),
            IncrementalHypothesis("Hallo Welt heute", "Hallo", 100),
        ],
        "Hallo Welt heute",
    )
    failing = _ScriptedRecognizer(
        [
            IncrementalHypothesis("Hallo Werlt", "Hallo", 100),
            IncrementalHypothesis("Hello world", "Hello", 110),
        ],
        "unused",
    )
    recognizer = IncrementalFusionRecognizer((StreamingModel("healthy", healthy), StreamingModel("failing", failing)))
    manager = StreamSessionManager(lambda *_args: recognizer)
    session = manager.create(
        filename="speech.pcm",
        language="de",
        media_type="audio/pcm;rate=16000;channels=1",
    )

    first = session.push(chunk_sequence=0, content=b"\x00\x00")
    second = session.push(chunk_sequence=1, content=b"\x01\x00")
    final = session.finalize()

    assert first.payload["finalized_until_ms"] == 100
    assert second.payload["text"].startswith("Hallo")
    assert second.payload["finalized_until_ms"] == 100
    failed = next(item for item in second.payload["candidates"] if item["backend"] == "failing")
    assert failed["status"] == "failed"
    assert failed["reason_code"] == "model_partial_failed"
    assert final.payload["result"]["text"] == "Hallo Welt heute"


def test_confirmed_stable_prefix_is_not_silently_revised() -> None:
    first = _ScriptedRecognizer(
        ["Alpha beta gamma", "Alpha beta gamma one", "Alfa beta gamma"],
        "Alpha beta final",
    )
    second = _ScriptedRecognizer(
        ["Alpha beta gamma", "Alpha beta gamma two", "Alfa beta delta"],
        "Alpha beta final",
    )
    recognizer = IncrementalFusionRecognizer((StreamingModel("alpha", first), StreamingModel("beta", second)))
    session = StreamSessionManager(lambda *_args: recognizer).create(
        filename="speech.pcm",
        language=None,
        media_type="audio/pcm;rate=16000;channels=1",
    )

    session.push(chunk_sequence=0, content=b"\x00\x00")
    confirmed = session.push(chunk_sequence=1, content=b"\x01\x00")
    revised = session.push(chunk_sequence=2, content=b"\x02\x00")

    assert confirmed.payload["stable_text"] == "Alpha beta"
    assert revised.payload["stable_text"] == "Alpha beta"
    assert revised.payload["text"].startswith("Alpha beta")
    assert revised.payload["stability_conflict"] is True


def test_incremental_fusion_keeps_session_backpressure_while_one_model_degrades() -> None:
    entered = threading.Event()
    release = threading.Event()

    class _SlowRecognizer(_ScriptedRecognizer):
        def accept(self, content: bytes):
            entered.set()
            release.wait(timeout=1)
            return super().accept(content)

    slow = _SlowRecognizer(["Hallo", "Hallo Welt"], "Hallo Welt")
    failing = _ScriptedRecognizer([RuntimeError("private backend detail")], "unused")
    recognizer = IncrementalFusionRecognizer((StreamingModel("alpha", slow), StreamingModel("beta", failing)))
    session = StreamSessionManager(lambda *_args: recognizer).create(
        filename="speech.pcm",
        language=None,
        media_type="audio/pcm;rate=16000;channels=1",
    )
    worker = threading.Thread(target=lambda: session.push(chunk_sequence=0, content=b"\x00\x00"))
    worker.start()
    assert entered.wait(timeout=1)
    with pytest.raises(StreamProtocolError) as exc_info:
        session.push(chunk_sequence=1, content=b"\x01\x00")
    assert exc_info.value.code == "stream.backpressure"
    release.set()
    worker.join(timeout=1)

    continued = session.push(chunk_sequence=1, content=b"\x01\x00")
    assert continued.event_type == "partial_fusion"
    failed = next(item for item in continued.payload["candidates"] if item["backend"] == "beta")
    assert failed["reason_code"] == "model_partial_failed"
    assert "private backend detail" not in str(continued.as_dict())


def test_policy_factory_requires_hub_streaming_fusion_and_two_native_models() -> None:
    class _NativeBackend:
        def __init__(self, backend_id: str) -> None:
            self.backend_id = backend_id

        def create_incremental_recognizer(self, **_kwargs):
            return _ScriptedRecognizer([self.backend_id], self.backend_id)

    class _Catalog:
        def __init__(self) -> None:
            self.backends = {
                "vosk": _NativeBackend("vosk"),
                "whisper_cpp": _NativeBackend("whisper_cpp"),
            }

        def available_backends(self, backend_ids=None):
            requested = set(backend_ids or self.backends)
            return {key: value for key, value in self.backends.items() if key in requested}

    class _Pipeline:
        def transcribe(self, **_kwargs):
            return TranscriptionResult(text="batch fallback")

    runtime = VoiceRuntimeConfig(transport_mode="stream", enable_streaming=True)
    context = VoiceRecognitionContext(
        configuration=HubVoiceConfiguration(
            transport_mode="streaming",
            recognition_strategy="parallel_compare",
            primary_backend="vosk",
            secondary_backends=("whisper_cpp",),
            feature_flags={"voice_fusion": True},
        )
    )
    factory = policy_streaming_recognizer_factory(_Pipeline(), _Catalog(), runtime)

    recognizer = factory("speech.pcm", "de", 1024, "audio/pcm;rate=16000;channels=1", context)

    assert isinstance(recognizer, IncrementalFusionRecognizer)

    runtime_only = VoiceRuntimeConfig(
        transport_mode="stream",
        recognition_strategy="parallel_compare",
        primary_backend="vosk",
        secondary_backends=("whisper_cpp",),
        enable_streaming=True,
    )
    runtime_only_factory = policy_streaming_recognizer_factory(_Pipeline(), _Catalog(), runtime_only)
    without_hub_policy = runtime_only_factory(
        "speech.pcm",
        "de",
        1024,
        "audio/pcm;rate=16000;channels=1",
        VoiceRecognitionContext(),
    )
    assert isinstance(without_hub_policy, BufferedPipelineRecognizer)
