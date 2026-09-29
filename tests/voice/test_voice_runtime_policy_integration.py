from __future__ import annotations

import io
import json
import types
import wave
from pathlib import Path

import pytest

from voice_runtime.app import create_app
from voice_runtime.backends.base import ChatResult, TranscriptionCandidate, TranscriptionResult, TranscriptionSegment
from voice_runtime.backends.faster_whisper import FasterWhisperBackend
from voice_runtime.backends.mock import MockVoiceBackend
from voice_runtime.backends.registry import VoiceBackendFactoryRegistry
from voice_runtime.backends.router import build_voice_backend_resolver
from voice_runtime.backends.whisper_cpp import WhisperCppBackend
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.diarization_adapters import DiarizationCapability, SpeakerTurn
from voice_runtime.errors import BackendUnavailableError, InvalidAudioError
from voice_runtime.fusion import CandidateLineageError, CandidateLineageValidator
from voice_runtime.pipeline import TranscriptionPipeline
from voice_runtime.streaming import (
    StreamSessionManager,
    buffered_pipeline_recognizer_factory,
    buffered_recognizer_factory,
)


def _wav_bytes(*, duration_ms: int = 100, offset: int = 500) -> bytes:
    frame_count = duration_ms * 16_000 // 1000
    frames = bytearray()
    for index in range(frame_count):
        sample = offset + (1_000 if index % 2 else -1_000)
        frames.extend(int(sample).to_bytes(2, "little", signed=True))
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(bytes(frames))
    return output.getvalue()


def _duration_ms(content: bytes) -> int:
    with wave.open(io.BytesIO(content), "rb") as source:
        return source.getnframes() * 1000 // source.getframerate()


def _pcm_bytes(content: bytes) -> bytes:
    with wave.open(io.BytesIO(content), "rb") as source:
        return source.readframes(source.getnframes())


class _Backend:
    def __init__(self, backend_id: str, calls: list[tuple[str, int]], *, regional: bool = False) -> None:
        self.backend_id = backend_id
        self.calls = calls
        self.regional = regional

    def name(self) -> str:
        return self.backend_id

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        duration = _duration_ms(content)
        self.calls.append((self.backend_id, duration))
        if self.regional:
            return TranscriptionResult(
                text="fixed",
                language=language or "de",
                duration_ms=duration,
                model=f"model-{self.backend_id}",
                confidence=0.95,
                raw_backend=self.backend_id,
                segments=(TranscriptionSegment(0, duration, "fixed", confidence=0.95, backend=self.backend_id),),
                provenance={"model_revision": f"rev-{self.backend_id}", "device": "cpu"},
            )
        if self.backend_id == "vosk" and duration == 100:
            segments = (
                TranscriptionSegment(0, 50, "good", confidence=0.9, backend=self.backend_id),
                TranscriptionSegment(50, 100, "bad", confidence=0.2, backend=self.backend_id),
            )
            text = "good bad"
            confidence = 0.55
        else:
            text = f"text-{self.backend_id}"
            confidence = 0.8 if self.backend_id == "vosk" else 0.9
            segments = (TranscriptionSegment(0, duration, text, confidence=confidence, backend=self.backend_id),)
        return TranscriptionResult(
            text=text,
            language=language or "de",
            duration_ms=duration,
            model=f"model-{self.backend_id}",
            confidence=confidence,
            raw_backend=self.backend_id,
            segments=segments,
            provenance={"model_revision": f"rev-{self.backend_id}", "device": "cpu"},
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return ChatResult(text=self.backend_id)

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()


def _hub_configuration(**overrides: object) -> VoiceRecognitionContext:
    configuration: dict[str, object] = {
        "transport_mode": "batch",
        "recognition_strategy": "single",
        "routing_strategy": "fallback",
        "correction_policy": "none",
        "review_policy": "on_disagreement",
        "primary_backend": "vosk",
        "secondary_backends": ["whisper_cpp"],
        "max_parallel_backends": 2,
        "candidate_deadline_sec": 30.0,
        "confidence_threshold": 0.7,
        "feature_flags": {},
    }
    configuration.update(overrides)
    return VoiceRecognitionContext.from_mapping({"configuration": configuration})


def _pipeline(
    config: VoiceRuntimeConfig,
    calls: list[tuple[str, int]],
    **kwargs: object,
) -> TranscriptionPipeline:
    backends = {
        "vosk": _Backend("vosk", calls),
        "whisper_cpp": _Backend("whisper_cpp", calls, regional=True),
        "faster_whisper": _Backend("faster_whisper", calls),
        "voxtral": _Backend("voxtral", calls),
    }
    return TranscriptionPipeline(
        config=config,
        backend=MockVoiceBackend(),
        backend_resolver=lambda backend_id: backends[backend_id],
        **kwargs,
    )


def _routed_pipeline(
    config: VoiceRuntimeConfig,
    calls: list[tuple[str, int]],
    factory_calls: dict[str, int],
) -> TranscriptionPipeline:
    registry = VoiceBackendFactoryRegistry()

    def register(backend_id: str, *, regional: bool = False) -> None:
        def factory(_config, _catalog):
            factory_calls[backend_id] = factory_calls.get(backend_id, 0) + 1
            return _Backend(backend_id, calls, regional=regional)

        registry.register(backend_id, factory)

    register("vosk")
    register("whisper_cpp", regional=True)
    resolver = build_voice_backend_resolver(
        config,
        backend_ids=("vosk", "whisper_cpp"),
        registry=registry,
    )
    return TranscriptionPipeline(
        config=config,
        backend=resolver.route(("vosk",)),
        backend_resolver=resolver,
    )


def _config(**overrides: object) -> VoiceRuntimeConfig:
    values: dict[str, object] = {
        "backend_fallback_order": ("mock",),
        "primary_backend": "mock",
        "secondary_backends": (),
        "max_parallel_backends": 2,
        "candidate_deadline_sec": 10.0,
        "policy_allowed_backends": ("vosk", "whisper_cpp", "faster_whisper", "voxtral"),
    }
    values.update(overrides)
    return VoiceRuntimeConfig(**values)


def test_hub_profile_and_session_configuration_changes_strategy_and_backend_selection() -> None:
    calls: list[tuple[str, int]] = []
    pipeline = _pipeline(_config(), calls)
    audio = _wav_bytes()

    profile = pipeline.transcribe(
        filename="profile.wav",
        content=audio,
        context=_hub_configuration(
            recognition_strategy="parallel_compare",
            feature_flags={"voice_fusion": True},
        ),
    )
    session = pipeline.transcribe(
        filename="session.wav",
        content=audio,
        context=_hub_configuration(primary_backend="whisper_cpp", secondary_backends=[]),
    )

    assert {candidate.backend for candidate in profile.candidates} == {"vosk", "whisper_cpp"}
    assert profile.fusion_strategy == "parallel_compare"
    assert session.raw_backend == "whisper_cpp"
    assert session.text == "fixed"
    assert next(stage for stage in session.stages if stage["stage"] == "execution_policy")["source"] == "hub_context"


def test_routed_catalog_reuses_backend_instances_across_parallel_requests() -> None:
    calls: list[tuple[str, int]] = []
    factory_calls: dict[str, int] = {}
    pipeline = _routed_pipeline(_config(backend_fallback_order=("vosk",)), calls, factory_calls)
    context = _hub_configuration(
        recognition_strategy="parallel_compare",
        feature_flags={"voice_fusion": True},
    )

    first = pipeline.transcribe(filename="first.wav", content=_wav_bytes(), context=context)
    second = pipeline.transcribe(filename="second.wav", content=_wav_bytes(), context=context)

    assert first.decision_trace["execution"] == "parallel"
    assert second.decision_trace["execution"] == "parallel"
    assert factory_calls == {"vosk": 1, "whisper_cpp": 1}


def test_routed_catalog_reuses_backend_instances_across_classic_then_correct_requests() -> None:
    calls: list[tuple[str, int]] = []
    factory_calls: dict[str, int] = {}
    pipeline = _routed_pipeline(_config(backend_fallback_order=("vosk",)), calls, factory_calls)
    context = _hub_configuration(recognition_strategy="classic_then_correct")

    first = pipeline.transcribe(filename="first.wav", content=_wav_bytes(), context=context)
    second = pipeline.transcribe(filename="second.wav", content=_wav_bytes(), context=context)

    assert first.decision_trace["execution"] == "sequential"
    assert second.decision_trace["execution"] == "sequential"
    assert factory_calls == {"vosk": 1, "whisper_cpp": 1}


def test_disabled_voice_fusion_flag_falls_back_to_compatible_single() -> None:
    calls: list[tuple[str, int]] = []
    result = _pipeline(_config(), calls).transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(recognition_strategy="parallel_compare", feature_flags={"voice_fusion": False}),
    )

    assert [backend for backend, _duration in calls] == ["vosk"]
    policy_stage = next(stage for stage in result.stages if stage["stage"] == "execution_policy")
    assert policy_stage["recognition_strategy"] == "single"
    assert policy_stage["adjustments"][0]["reason_code"] == "voice_fusion_disabled"


def test_context_blocks_unknown_administrative_and_feature_fields() -> None:
    with pytest.raises(ValueError, match="administrative"):
        VoiceRecognitionContext.from_mapping({"configuration": {"model_paths": {"vosk": "/tmp/model"}}})
    with pytest.raises(ValueError, match="feature_flags"):
        VoiceRecognitionContext.from_mapping({"configuration": {"feature_flags": {"download_models": True}}})


def test_hub_budgets_are_clamped_to_runtime_maxima() -> None:
    calls: list[tuple[str, int]] = []
    config = _config(max_parallel_backends=1, candidate_deadline_sec=5.0, max_candidate_count=1)
    result = _pipeline(config, calls).transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(
            recognition_strategy="parallel_compare",
            max_parallel_backends=4,
            candidate_deadline_sec=300.0,
            feature_flags={"voice_fusion": True},
        ),
    )
    policy_stage = next(stage for stage in result.stages if stage["stage"] == "execution_policy")
    assert policy_stage["max_parallel_backends"] == 1
    assert policy_stage["max_candidate_count"] == 1
    assert policy_stage["candidate_deadline_sec"] == 5.0
    assert len(result.candidates) == 1


def test_enhancement_variants_preserve_request_lineage_without_double_weighting() -> None:
    calls: list[tuple[str, int]] = []
    config = _config(
        audio_enhancement_enabled=True,
        enhancement_variants=("original", "normalized"),
        max_candidate_count=4,
    )
    pipeline = _pipeline(config, calls)
    context = _hub_configuration(
        recognition_strategy="parallel_compare",
        feature_flags={"voice_fusion": True},
    )

    first = pipeline.transcribe(filename="sample.wav", content=_wav_bytes(), context=context)
    second = pipeline.transcribe(filename="sample.wav", content=_wav_bytes(), context=context)

    assert len(first.candidates) == 4
    for backend in ("vosk", "whisper_cpp"):
        related = [candidate for candidate in first.candidates if candidate.backend == backend]
        assert len({candidate.lineage_id for candidate in related}) == 1
        assert sum(not candidate.parent_candidate_ids for candidate in related) == 1
        assert sum(bool(candidate.parent_candidate_ids) for candidate in related) == 1
    assert first.text == second.text
    assert [candidate.text for candidate in first.candidates] == [
        candidate.text for candidate in second.candidates
    ]
    assert first.decision_trace["result_hash"] != second.decision_trace["result_hash"]
    enhancement = next(stage for stage in first.stages if stage["stage"] == "audio_enhancement")
    assert enhancement["profiles"] == ["original", "normalized"]


def test_pcm_identical_bypass_variant_is_not_executed_twice() -> None:
    calls: list[tuple[str, int]] = []
    result = _pipeline(
        _config(
            audio_enhancement_enabled=True,
            enhancement_variants=("original", "bypass"),
            max_candidate_count=4,
        ),
        calls,
    ).transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(
            recognition_strategy="parallel_compare",
            feature_flags={"voice_fusion": True},
        ),
    )
    stage = next(item for item in result.stages if item["stage"] == "audio_enhancement")
    assert len(result.candidates) == 2
    assert stage["duplicate_variants"] == 1


def test_lineage_validator_rejects_missing_parents_cycles_and_model_provenance_changes() -> None:
    validator = CandidateLineageValidator()
    root = TranscriptionCandidate(
        candidate_id="root",
        backend="vosk",
        model="model-a",
        model_revision="rev-a",
        audio_variant_id="original",
        source_audio_digest="sha256:audio",
        lineage_id="root",
        text="root",
    )
    missing = TranscriptionCandidate(
        candidate_id="missing-child",
        backend="vosk",
        source_audio_digest="sha256:audio",
        lineage_id="root",
        parent_candidate_ids=("absent",),
        provenance={"lineage_relation": "context_derived"},
    )
    with pytest.raises(CandidateLineageError, match="missing parent"):
        validator.validate((root, missing))

    cycle_a = TranscriptionCandidate(
        candidate_id="cycle-a",
        backend="vosk",
        lineage_id="cycle-a",
        parent_candidate_ids=("cycle-b",),
        provenance={"lineage_relation": "context_derived"},
    )
    cycle_b = TranscriptionCandidate(
        candidate_id="cycle-b",
        backend="vosk",
        lineage_id="cycle-a",
        parent_candidate_ids=("cycle-a",),
        provenance={"lineage_relation": "context_derived"},
    )
    with pytest.raises(CandidateLineageError, match="cycle"):
        validator.validate((cycle_a, cycle_b))

    inconsistent = TranscriptionCandidate(
        candidate_id="variant",
        backend="vosk",
        model="different-model",
        model_revision="rev-b",
        audio_variant_id="normalized",
        source_audio_digest="sha256:audio",
        lineage_id="root",
        text="variant",
        parent_candidate_ids=("root",),
        provenance={"lineage_relation": "audio_variant", "audio_variant_profile": "normalized"},
    )
    with pytest.raises(CandidateLineageError, match="model provenance"):
        validator.validate((root, inconsistent))


class _Diarizer:
    adapter_id = "pyannote"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def capability(self) -> DiarizationCapability:
        return DiarizationCapability("pyannote", True, None)

    def diarize(self, _audio) -> tuple[SpeakerTurn, ...]:
        if self.fail:
            raise BackendUnavailableError("offline model unavailable")
        return (SpeakerTurn(0, 100, "SPEAKER_01"),)


@pytest.mark.parametrize(("fail", "expected_speaker"), [(False, "SPEAKER_01"), (True, None)])
def test_pyannote_integration_is_explicit_local_and_failure_safe(fail: bool, expected_speaker: str | None) -> None:
    calls: list[tuple[str, int]] = []
    config = _config(
        diarization_backend="pyannote",
        pyannote_manifest_path="/configured/manifest.json",
        diarization_model_root="/configured/models",
    )
    result = _pipeline(config, calls, diarization_adapter=_Diarizer(fail=fail)).transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(feature_flags={"optional_models": True}),
    )
    assert result.segments[0].speaker == expected_speaker
    stage = next(item for item in result.stages if item["stage"] == "diarization")
    assert stage["status"] == ("skipped" if fail else "succeeded")
    assert result.text == "good bad"


def test_adaptive_local_reruns_only_calibrated_low_confidence_region(tmp_path: Path) -> None:
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "schema_version": "ananta.voice-calibration.v1",
                "profiles": [
                    {
                        "backend": "vosk",
                        "model_revision": "rev-vosk",
                        "dataset_version": "fixture-v1",
                        "calibrator_version": "linear-v1",
                        "slope": 1.0,
                        "intercept": 0.0,
                        "evaluation": {
                            "sample_count": 100,
                            "ece_before": 0.2,
                            "ece_after": 0.1,
                            "brier_before": 0.3,
                            "brier_after": 0.2,
                        },
                        "thresholds": {
                            "version": "fixture-thresholds-v1",
                            "minimum_confidence": 0.7,
                            "language": "*",
                            "hardware_profile": "*",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, int]] = []
    result = _pipeline(_config(calibration_path=str(calibration)), calls).transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(
            routing_strategy="adaptive",
            feature_flags={"optional_models": True},
        ),
    )

    assert result.text == "good fixed"
    assert calls == [("vosk", 100), ("whisper_cpp", 50)]
    assert [(segment.start_ms, segment.end_ms, segment.text) for segment in result.segments] == [
        (0, 50, "good"),
        (50, 100, "fixed"),
    ]
    stage = next(item for item in result.stages if item["stage"] == "adaptive_routing")
    assert stage["rerun_count"] == 1


def test_restricted_and_generative_correction_remain_strictly_hub_side() -> None:
    calls: list[tuple[str, int]] = []
    pipeline = _pipeline(
        _config(restricted_choice_hook_enabled=True, generative_judge_hook_enabled=True),
        calls,
    )
    restricted = pipeline.transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(
            recognition_strategy="parallel_compare",
            correction_policy="restricted_choice",
            feature_flags={"voice_fusion": True, "restricted_worker": True},
        ),
    )
    restricted_stage = next(item for item in restricted.stages if item["stage"] == "correction_boundary")
    assert restricted_stage["status"] == "hub_postprocessing_required"
    assert restricted_stage["runtime_worker_call"] is False
    assert restricted_stage["no_generation"] is True

    generative = pipeline.transcribe(
        filename="sample.wav",
        content=_wav_bytes(),
        context=_hub_configuration(
            correction_policy="generative_local",
            feature_flags={"generative_judge": True},
        ),
    )
    generative_stage = next(item for item in generative.stages if item["stage"] == "correction_boundary")
    assert generative_stage["status"] == "hub_postprocessing_required"
    assert generative_stage["runtime_worker_call"] is False


def test_invalid_audio_is_rejected_before_any_real_backend_or_fallback_runs() -> None:
    calls: list[tuple[str, int]] = []
    pipeline = _pipeline(_config(), calls)
    with pytest.raises(InvalidAudioError):
        pipeline.transcribe(
            filename="broken.wav",
            content=b"not-a-wave",
            context=_hub_configuration(),
        )
    assert calls == []


def test_invalid_audio_http_response_is_typed_and_does_not_reach_vosk() -> None:
    app = create_app(
        VoiceRuntimeConfig(
            backend_fallback_order=("vosk",),
            asr_backend="vosk",
            primary_backend="vosk",
            rerun_backend="vosk",
        )
    )
    response = app.test_client().post(
        "/v1/audio/transcriptions",
        data={"file": (io.BytesIO(b"not-a-wave"), "broken.wav")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "voice.invalid_input"


def test_runtime_capabilities_expose_policy_and_no_worker_to_worker_boundary() -> None:
    app = create_app(VoiceRuntimeConfig(backend_fallback_order=("mock",)))
    payload = app.test_client().get("/v1/models").get_json()
    capabilities = payload["runtime_capabilities"]
    assert capabilities["policy"]["owner"] == "hub"
    assert capabilities["strict_audio_decode_before_fanout"] is True
    assert capabilities["enhancement"]["lineage_deduplication"] is True
    assert capabilities["judge_boundary"]["runtime_worker_to_worker_calls"] is False


def test_stream_manager_applies_hub_execution_context_at_finalization() -> None:
    calls: list[tuple[str, int]] = []
    pipeline = _pipeline(_config(), calls)
    context = _hub_configuration(
        recognition_strategy="parallel_compare",
        feature_flags={"voice_fusion": True},
    )
    manager = StreamSessionManager(
        buffered_recognizer_factory(MockVoiceBackend()),
        policy_recognizer_factory=buffered_pipeline_recognizer_factory(pipeline),
    )
    session = manager.create(
        filename="stream.pcm",
        language="de",
        media_type="audio/pcm;rate=16000;channels=1",
        recognition_context=context,
        execution_policy={"source": "hub_context", "recognition_strategy": "parallel_compare"},
    )
    session.push(chunk_sequence=0, content=_pcm_bytes(_wav_bytes()))
    final = session.finalize()

    result = final.payload["result"]
    assert isinstance(result, dict)
    assert {item["backend"] for item in result["candidates"]} == {"vosk", "whisper_cpp"}
    assert session.snapshot()["execution_policy"]["recognition_strategy"] == "parallel_compare"


def test_stream_create_rejects_administrative_policy_context() -> None:
    app = create_app(VoiceRuntimeConfig(enable_streaming=True, backend_fallback_order=("mock",)))
    response = app.test_client().post(
        "/v1/audio/streams",
        json={
            "filename": "stream.pcm",
            "media_type": "audio/pcm;rate=16000;channels=1",
            "recognition_context": {"configuration": {"service_tokens": {"voice": "secret"}}},
        },
    )
    assert response.status_code == 422
    assert response.json["error"]["code"] == "voice.invalid_context"


def test_typed_whisper_tuning_and_faster_whisper_vad_are_forwarded(tmp_path: Path) -> None:
    whisper = WhisperCppBackend(
        binary="/bin/echo",
        model_path="/models/model.bin",
        threads=6,
        gpu_layers=12,
        beam_size=3,
        temperature=0.25,
        prompt_max_chars=8,
    )
    argv = whisper.build_argv(
        input_path="/tmp/input.wav",
        output_path="/tmp/output.json",
        language="de",
        initial_prompt="Ananta very long prompt",
    )
    assert argv[argv.index("--threads") + 1] == "6"
    assert "--gpu-layers" not in argv
    assert "--no-gpu" not in argv
    assert argv[argv.index("--beam-size") + 1] == "3"
    assert argv[argv.index("--temperature") + 1] == "0.25"
    assert argv[argv.index("--prompt") + 1] == "Ananta v"

    model_path = tmp_path / "faster"
    model_path.mkdir()
    observed: dict[str, object] = {}

    class Model:
        def __init__(self, _path: str, **_kwargs: object) -> None:
            pass

        def transcribe(self, _path: str, **kwargs: object):
            observed.update(kwargs)
            return iter(()), types.SimpleNamespace(language="de")

    FasterWhisperBackend(
        model_path=str(model_path),
        model_factory=Model,
        compute_type="int8",
        beam_size=7,
        vad_filter=True,
        vad_min_silence_ms=650,
    ).transcribe(filename="sample.wav", content=_wav_bytes())
    assert observed["beam_size"] == 7
    assert observed["vad_filter"] is True
    assert observed["vad_parameters"] == {"min_silence_duration_ms": 650}


def test_whisper_cpp_projects_classic_transcript_into_the_bounded_local_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = WhisperCppBackend(
        binary="/bin/echo",
        model_path="/models/model.bin",
        prompt_max_chars=32,
    )
    observed: dict[str, object] = {}

    def transcribe(**kwargs: object) -> TranscriptionResult:
        observed.update(kwargs)
        return TranscriptionResult(text="corrected", raw_backend="whisper_cpp")

    monkeypatch.setattr(backend, "_transcribe", transcribe)
    result = backend.transcribe_with_context(
        filename="sample.wav",
        content=b"audio",
        language="de",
        context={
            "hotwords": ["Ananta"],
            "classic_transcript": "klassische Transkription",
            "previous_segment_context": "vorher",
        },
    )

    assert "transcript_reference" in backend.context_capabilities()
    assert observed["initial_prompt"] == "Ananta klassische Transkription vorher"
    assert len(backend._bounded_prompt(str(observed["initial_prompt"]))) == 32
    assert result.text == "corrected"
