from __future__ import annotations

import io
import threading
import time
import wave

import pytest

from voice_runtime.backends.base import (
    ChatResult,
    TranscriptionResult,
    TranscriptionSegment,
)
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.errors import VoiceRuntimeError
from voice_runtime.execution_policy import HubVoiceConfiguration, VoiceExecutionPolicy
from voice_runtime.parallel import CandidateExecutionPolicy, ParallelCandidateExecutor
from voice_runtime.pipeline import TranscriptionPipeline
from voice_runtime.resources import (
    BackendResourceRequirement,
    ResourceAdmissionController,
    VoiceResourceBudget,
)


def _wav_bytes(*, duration_ms: int = 400) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * (16_000 * duration_ms // 1000))
    return buffer.getvalue()


class _ControlledBackend:
    def __init__(
        self,
        backend_id: str,
        *,
        delay_seconds: float = 0.0,
        requirement: BackendResourceRequirement | None = None,
        capabilities: frozenset[str] = frozenset(),
    ) -> None:
        self.backend_id = backend_id
        self.delay_seconds = delay_seconds
        self.requirement = requirement or BackendResourceRequirement()
        self.capabilities = capabilities
        self.calls = 0
        self.cancel_requested = threading.Event()
        self.contexts: list[dict[str, object]] = []

    def name(self) -> str:
        return self.backend_id

    def resource_requirements(self) -> BackendResourceRequirement:
        return self.requirement

    def transcribe_with_control(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, object],
        cancellation_token,
        deadline_monotonic: float,
    ) -> TranscriptionResult:
        del filename, content, deadline_monotonic
        self.calls += 1
        self.contexts.append(dict(context))
        until = time.monotonic() + self.delay_seconds
        while time.monotonic() < until:
            cancellation_token.raise_if_cancelled()
            time.sleep(0.002)
        cancellation_token.raise_if_cancelled()
        return TranscriptionResult(
            text=self.backend_id,
            language=language,
            duration_ms=100,
            confidence=0.8,
            raw_backend=self.backend_id,
        )

    def cancel_transcription(self, *, cancellation_token) -> None:
        del cancellation_token
        self.cancel_requested.set()

    def transcribe(self, **_kwargs) -> TranscriptionResult:
        raise AssertionError("controlled execution contract was bypassed")

    def audio_chat(self, **_kwargs) -> ChatResult:
        return ChatResult(text="unused")

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return self.capabilities


def _budget(
    *,
    ram_bytes: int = 1024,
    vram_bytes: int = 1024,
    concurrency: int = 2,
    audio_ms: int = 10_000,
    queue: int = 4,
) -> VoiceResourceBudget:
    return VoiceResourceBudget(
        max_ram_bytes=ram_bytes,
        max_vram_bytes=vram_bytes,
        max_concurrent_backends=concurrency,
        max_audio_ms=audio_ms,
        max_queue_depth=queue,
    )


def test_resource_admission_denies_ram_vram_and_audio_before_backend_start() -> None:
    controller = ResourceAdmissionController(
        _budget(ram_bytes=64, vram_bytes=0, audio_ms=500)
    )
    backend = _ControlledBackend(
        "too-large",
        requirement=BackendResourceRequirement(ram_bytes=65, vram_bytes=1),
    )
    executor = ParallelCandidateExecutor(admission_controller=controller)

    candidates = executor.execute(
        {"too-large": backend},
        filename="sample.wav",
        content=b"audio",
        language=None,
        policy=CandidateExecutionPolicy(
            audio_duration_ms=600,
            resource_budget=_budget(
                ram_bytes=10_000,
                vram_bytes=10_000,
                audio_ms=10_000,
            ),
        ),
    )

    assert backend.calls == 0
    assert candidates[0].error.code == "resource_exhausted"
    assert controller.effective_budget(_budget(ram_bytes=9999)).max_ram_bytes == 64


def test_single_pipeline_also_applies_admission_before_backend_start() -> None:
    backend = _ControlledBackend(
        "mock",
        requirement=BackendResourceRequirement(ram_bytes=2 * 1024 * 1024),
    )
    pipeline = TranscriptionPipeline(
        config=VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            resource_max_ram_mb=1,
        ),
        backend=backend,
    )

    with pytest.raises(VoiceRuntimeError) as exc_info:
        pipeline.transcribe(filename="sample.wav", content=b"mock-audio")

    assert exc_info.value.code == "resource_exhausted"
    assert backend.calls == 0


def test_per_backend_deadline_cancels_only_slow_candidate_and_keeps_success() -> None:
    fast = _ControlledBackend("fast", delay_seconds=0.005)
    slow = _ControlledBackend("slow", delay_seconds=0.2)
    executor = ParallelCandidateExecutor(
        admission_controller=ResourceAdmissionController(_budget())
    )

    started = time.monotonic()
    candidates = executor.execute(
        {"fast": fast, "slow": slow},
        filename="sample.wav",
        content=b"audio",
        language="de",
        policy=CandidateExecutionPolicy(
            max_parallel_backends=2,
            deadline_seconds=0.3,
            backend_deadline_seconds={"fast": 0.1, "slow": 0.03},
            audio_duration_ms=100,
            resource_budget=_budget(),
        ),
    )

    assert time.monotonic() - started < 0.15
    assert next(item for item in candidates if item.backend == "fast").status == "succeeded"
    timed_out = next(item for item in candidates if item.backend == "slow")
    assert timed_out.error.code == "timeout"
    assert slow.cancel_requested.wait(timeout=0.1)


class _ClassicBackend:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.segment = TranscriptionSegment(0, 100, "classic exact", confidence=0.8)

    def name(self) -> str:
        return "vosk"

    def transcribe(self, **_kwargs) -> TranscriptionResult:
        self.events.append("classic_finished")
        return TranscriptionResult(
            text="classic exact",
            duration_ms=100,
            confidence=0.8,
            raw_backend="vosk",
            segments=(self.segment,),
            provenance={"model_revision": "classic-v1", "device": "cpu"},
        )

    def audio_chat(self, **_kwargs) -> ChatResult:
        return ChatResult(text="unused")

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()


class _FailingCorrector(_ClassicBackend):
    def name(self) -> str:
        return "whisper_cpp"

    def context_capabilities(self) -> frozenset[str]:
        return frozenset({"transcript_reference"})

    def transcribe_with_context(self, *, context: dict[str, object], **_kwargs) -> TranscriptionResult:
        assert self.events == ["classic_finished"]
        assert context["classic_transcript"] == "classic exact"
        self.events.append("corrector_started")
        raise RuntimeError("private corrector failure")


def test_classic_then_correct_preserves_completed_classic_on_corrector_failure() -> None:
    events: list[str] = []
    classic = _ClassicBackend(events)
    corrector = _FailingCorrector(events)
    backends = {"vosk": classic, "whisper_cpp": corrector}
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        recognition_strategy="classic_then_correct",
        primary_backend="vosk",
        secondary_backends=("whisper_cpp",),
        candidate_deadline_sec=1.0,
    )
    pipeline = TranscriptionPipeline(
        config=config,
        backend=classic,
        backend_resolver=lambda backend_id: backends[backend_id],
    )

    result = pipeline.transcribe(filename="sample.wav", content=_wav_bytes(duration_ms=100))

    assert events == ["classic_finished", "corrector_started"]
    assert result.text == "classic exact"
    assert result.segments == (classic.segment,)
    assert (result.segments[0].start_ms, result.segments[0].end_ms) == (0, 100)
    assert result.selected_candidate_id == result.candidates[0].candidate_id
    assert [item.status for item in result.candidates] == ["succeeded", "failed"]
    assert result.decision_trace["execution"] == "sequential"
    assert result.decision_trace["classic_preserved"] is True
    assert result.decision_trace["corrector_reason_code"] == "model_error"
    assert "private corrector failure" not in str(result.as_dict())


def test_classic_then_correct_preserves_classic_on_corrector_timeout() -> None:
    events: list[str] = []
    classic = _ClassicBackend(events)
    corrector = _ControlledBackend(
        "whisper_cpp",
        delay_seconds=0.3,
        capabilities=frozenset({"transcript_reference"}),
    )
    backends = {"vosk": classic, "whisper_cpp": corrector}
    pipeline = TranscriptionPipeline(
        config=VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            recognition_strategy="classic_then_correct",
            primary_backend="vosk",
            secondary_backends=("whisper_cpp",),
            candidate_deadline_sec=0.1,
        ),
        backend=classic,
        backend_resolver=lambda backend_id: backends[backend_id],
    )

    result = pipeline.transcribe(
        filename="sample.wav",
        content=_wav_bytes(duration_ms=100),
    )

    assert result.text == "classic exact"
    assert result.decision_trace["classic_preserved"] is True
    assert result.decision_trace["corrector_reason_code"] == "timeout"
    assert corrector.contexts[0]["classic_transcript"] == "classic exact"
    assert corrector.cancel_requested.wait(timeout=0.1)


class _SelectiveBackend(_ClassicBackend):
    def __init__(self) -> None:
        super().__init__([])
        self.low = TranscriptionSegment(0, 100, "low", confidence=0.2)
        self.high = TranscriptionSegment(100, 200, "high", confidence=0.95)
        self.over_budget = TranscriptionSegment(200, 400, "large", confidence=0.1)

    def transcribe(self, **_kwargs) -> TranscriptionResult:
        return TranscriptionResult(
            text="low high large",
            duration_ms=400,
            confidence=0.4,
            raw_backend="vosk",
            segments=(self.low, self.high, self.over_budget),
        )


class _RegionalCorrector(_ClassicBackend):
    def __init__(self) -> None:
        super().__init__([])
        self.calls = 0

    def name(self) -> str:
        return "whisper_cpp"

    def transcribe(self, **_kwargs) -> TranscriptionResult:
        self.calls += 1
        return TranscriptionResult(
            text="fixed",
            duration_ms=100,
            confidence=0.9,
            raw_backend="whisper_cpp",
            segments=(TranscriptionSegment(0, 100, "fixed", confidence=0.9),),
        )


def test_selective_rerun_checks_segment_and_audio_budget_before_each_start() -> None:
    baseline = _SelectiveBackend()
    rerun = _RegionalCorrector()
    backends = {"vosk": baseline, "whisper_cpp": rerun}
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        transcription_pipeline="confidence_rerun",
        asr_backend="vosk",
        primary_backend="vosk",
        confidence_rerun_enabled=True,
        confidence_threshold=0.7,
        rerun_backend="whisper_cpp",
        rerun_max_segments=3,
        rerun_max_audio_ms=150,
    )
    pipeline = TranscriptionPipeline(
        config=config,
        backend=baseline,
        backend_resolver=lambda backend_id: backends[backend_id],
    )

    result = pipeline.transcribe(filename="sample.wav", content=_wav_bytes())

    assert rerun.calls == 1
    assert [segment.text for segment in result.segments] == ["fixed", "high", "large"]
    assert result.segments[1] is baseline.high
    assert result.segments[2] is baseline.over_budget
    trace = result.decision_trace["confidence_rerun"]
    assert trace["full_audio_fallback"] is False
    assert [item["status"] for item in trace["outcomes"]] == [
        "applied",
        "budget_skipped",
    ]


def test_hub_resource_request_can_only_narrow_runtime_budget() -> None:
    runtime = VoiceRuntimeConfig(
        resource_max_ram_mb=64,
        resource_max_vram_mb=32,
        resource_max_concurrent_backends=2,
        resource_max_audio_seconds=120,
        resource_max_queue_depth=4,
    )
    expanded = VoiceExecutionPolicy.resolve(
        runtime,
        HubVoiceConfiguration.from_mapping(
            {
                "resource_max_ram_mb": 1024,
                "resource_max_vram_mb": 1024,
                "resource_max_concurrent_backends": 4,
                "resource_max_audio_seconds": 1000,
                "resource_max_queue_depth": 20,
            }
        ),
    )
    narrowed = VoiceExecutionPolicy.resolve(
        runtime,
        HubVoiceConfiguration.from_mapping(
            {
                "resource_max_ram_mb": 32,
                "resource_max_vram_mb": 16,
                "resource_max_concurrent_backends": 1,
                "resource_max_audio_seconds": 30,
                "resource_max_queue_depth": 2,
            }
        ),
    )

    assert expanded.resource_budget.max_ram_bytes == 64 * 1024 * 1024
    assert expanded.resource_budget.max_concurrent_backends == 2
    assert narrowed.resource_budget.max_ram_bytes == 32 * 1024 * 1024
    assert narrowed.resource_budget.max_concurrent_backends == 1
