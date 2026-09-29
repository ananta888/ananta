from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Mapping

import pytest

from voice_runtime.backends.base import TranscriptionSegment, TranscriptionWord
from voice_runtime.diarization_adapters import (
    OfflineDiarizationManifest,
    PyannoteDiarizationAdapter,
    SafeDiarizationProcessor,
    SpeakerTurn,
    assign_speakers_by_overlap,
    compute_model_bundle_sha256,
)
from voice_runtime.errors import BackendUnavailableError, PolicyBlockedError
from voice_runtime.fusion.local_judge import (
    GenerativeJudgeRequest,
    LocalGenerativeJudge,
    LocalGenerativeJudgePolicy,
    StrictChoice,
    StrictChoiceJudge,
    StrictChoiceRequest,
    validate_loopback_endpoint,
)
from voice_runtime.preprocessing.audio_decode import DecodedPcmAudio
from voice_runtime.preprocessing.audio_enhancement import (
    BypassProcessor,
    ChannelMixProcessor,
    DcOffsetRemovalProcessor,
    DeterministicAudioEnhancementPipeline,
    HighPassProcessor,
    LazyLocalEnhancementProcessor,
    LimiterProcessor,
    LocalEnhancementEntrypoint,
    PeakNormalizationProcessor,
    ResampleProcessor,
    analyze_pcm_quality,
    compare_transcript_error_rates,
)
from voice_runtime.routing.adaptive import (
    AdaptiveLocalRouter,
    BackendRoute,
    ConfidenceRegion,
    RerunRegion,
    RoutingMeasurements,
    RoutingPolicyEnvelope,
    merge_regional_segments,
)


def _audio(
    samples: list[int],
    *,
    sample_rate_hz: int = 1_000,
    channels: int = 1,
    timeline_start_ms: int = 0,
) -> DecodedPcmAudio:
    frame_count = len(samples) // channels
    return DecodedPcmAudio(
        filename="fixture.wav",
        pcm_s16le=struct.pack(f"<{len(samples)}h", *samples),
        sample_rate_hz=sample_rate_hz,
        duration_ms=round(frame_count * 1000 / sample_rate_hz),
        source_format="wav",
        timeline_start_ms=timeline_start_ms,
        channels=channels,
    )


def _samples(audio: DecodedPcmAudio) -> tuple[int, ...]:
    return struct.unpack(f"<{len(audio.pcm_s16le) // 2}h", audio.pcm_s16le)


def test_enhancement_bypass_is_pcm_identical_and_variant_ids_are_request_scoped() -> None:
    audio = _audio([100, -100, 200, -200], timeline_start_ms=2_000)
    pipeline = DeterministicAudioEnhancementPipeline()
    original = pipeline.original_variant(audio)

    first = pipeline.run(original, [BypassProcessor()], label="bypass")
    second = pipeline.run(original, [BypassProcessor()], label="bypass")

    assert first.audio is audio
    assert first.audio.pcm_s16le == audio.pcm_s16le
    assert first.variant_id == second.variant_id
    assert DeterministicAudioEnhancementPipeline().original_variant(audio).variant_id != original.variant_id
    assert first.parent_variant_id == original.variant_id
    assert first.lineage == (original.variant_id,)
    assert first.time_axis.variant_to_source_ms(2_002) == 2_002
    assert first.steps[0].runtime_ms >= 0
    serialized = json.dumps(first.as_metadata(), sort_keys=True)
    assert first.steps[0].input_pcm_sha256 not in serialized
    assert first.steps[0].output_pcm_sha256 not in serialized


def test_builtin_enhancement_processors_have_isolated_deterministic_effects() -> None:
    offset = DcOffsetRemovalProcessor().transform(_audio([1_000, 1_000, 1_000, 1_000]))
    assert _samples(offset) == (0, 0, 0, 0)
    assert analyze_pcm_quality(offset).dc_offset == 0

    normalized = PeakNormalizationProcessor(target_peak=0.5, max_gain=20).transform(_audio([1_000, -1_000]))
    assert max(abs(value) for value in _samples(normalized)) == round(0.5 * 32767)

    high_pass = HighPassProcessor(cutoff_hz=80).transform(_audio([1_000] * 100, sample_rate_hz=1_000))
    assert abs(_samples(high_pass)[-1]) < abs(_samples(high_pass)[0])

    limited = LimiterProcessor(threshold=0.25).transform(_audio([30_000, -30_000]))
    assert max(abs(value) for value in _samples(limited)) <= round(0.25 * 32767)

    mixed = ChannelMixProcessor().transform(_audio([1_000, -1_000, 2_000, 0], channels=2))
    assert mixed.channels == 1
    assert _samples(mixed) == (0, 1_000)

    resampled = ResampleProcessor(target_sample_rate_hz=8).transform(_audio([0, 1_000, 2_000, 3_000], sample_rate_hz=4))
    assert resampled.sample_rate_hz == 8
    assert len(_samples(resampled)) == 8
    assert resampled.duration_ms == 1_000

    benchmark = compare_transcript_error_rates(
        reference="one two",
        baseline_text="one two",
        variant_text="wrong words",
    )
    assert benchmark.wer_delta > 0
    assert benchmark.cer_delta > 0
    assert benchmark.degraded is True


def test_lazy_enhancement_adapter_loads_on_first_use_and_denies_downloads() -> None:
    calls: list[str] = []

    def loader(entrypoint: LocalEnhancementEntrypoint):
        calls.append(entrypoint.adapter_id)

        def enhance(pcm: bytes, _rate: int, _channels: int, _parameters: Mapping[str, object]) -> bytes:
            return pcm

        return enhance

    entrypoint = LocalEnhancementEntrypoint(adapter_id="rnnoise-test", module_name="optional_rnnoise")
    processor = LazyLocalEnhancementProcessor(entrypoint=entrypoint, loader=loader)
    assert processor.capability().available is True
    assert calls == []

    audio = _audio([1, 2, 3, 4])
    assert processor.transform(audio).pcm_s16le == audio.pcm_s16le
    assert processor.transform(audio).pcm_s16le == audio.pcm_s16le
    assert calls == ["rnnoise-test"]

    with pytest.raises(PolicyBlockedError):
        LazyLocalEnhancementProcessor(entrypoint=entrypoint, downloads_allowed=True)
    with pytest.raises(PolicyBlockedError):
        LazyLocalEnhancementProcessor(entrypoint=entrypoint, parameters={"model_url": "https://example.invalid/model"})


def test_lazy_enhancement_adapter_reports_missing_dependency_without_breaking_core() -> None:
    processor = LazyLocalEnhancementProcessor(
        entrypoint=LocalEnhancementEntrypoint(
            adapter_id="missing",
            module_name="ananta_dependency_that_does_not_exist",
        )
    )
    assert processor.capability().reason_code == "dependency_unavailable"
    with pytest.raises(BackendUnavailableError):
        processor.transform(_audio([1, 2]))
    assert BypassProcessor().transform(_audio([1, 2])).pcm_s16le == _audio([1, 2]).pcm_s16le


class _Turn:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _Annotation:
    def itertracks(self, *, yield_label: bool):
        assert yield_label is True
        return iter(
            (
                (_Turn(0.0, 1.2), "track-1", "SPEAKER_02"),
                (_Turn(1.0, 2.0), "track-2", "SPEAKER_01"),
            )
        )


class _Pipeline:
    def __call__(self, _audio_input: object) -> _Annotation:
        return _Annotation()


def _diarization_manifest(model_path: Path) -> OfflineDiarizationManifest:
    return OfflineDiarizationManifest(
        provider="pyannote",
        model_id="speaker-diarization",
        revision="release-3.1-pinned",
        license_id="MIT",
        local_path=str(model_path),
        bundle_sha256=compute_model_bundle_sha256(model_path),
    )


def test_pyannote_adapter_is_lazy_offline_and_assigns_absolute_overlap(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "config.yaml").write_text("version: 1\n", encoding="utf-8")
    loads: list[Path] = []
    adapter = PyannoteDiarizationAdapter(
        manifest=_diarization_manifest(model_path),
        allowed_model_roots=(tmp_path,),
        pipeline_loader=lambda path: loads.append(path) or _Pipeline(),
        tensor_builder=lambda audio: {"duration_ms": audio.duration_ms},
        offline_guard=lambda: None,
    )
    assert adapter.capability().available is True
    assert loads == []

    words = (TranscriptionWord(5_100, 5_200, "hello"),)
    segments = (
        TranscriptionSegment(5_000, 6_000, "hello", words=words),
        TranscriptionSegment(6_000, 7_000, "world"),
    )
    outcome = SafeDiarizationProcessor(adapter).process(
        audio=_audio([0] * 2_000, timeline_start_ms=5_000),
        segments=segments,
    )

    assert loads == [model_path]
    assert outcome.status == "succeeded"
    assert outcome.segments[0].speaker == "SPEAKER_02"
    assert outcome.segments[1].speaker == "SPEAKER_01"
    assert outcome.segments[0].words is words
    assert outcome.segments[0].words[0].start_ms == 5_100


def test_diarization_overlap_tie_is_stable_and_failure_preserves_asr() -> None:
    segment = TranscriptionSegment(100, 200, "unchanged")
    assigned = assign_speakers_by_overlap(
        (segment,),
        (SpeakerTurn(100, 150, "B"), SpeakerTurn(150, 200, "A")),
    )
    assert assigned[0].speaker == "A"

    class FailingAdapter:
        adapter_id = "failing"

        def diarize(self, _audio: DecodedPcmAudio) -> tuple[SpeakerTurn, ...]:
            raise BackendUnavailableError("not installed")

    result = SafeDiarizationProcessor(FailingAdapter()).process(audio=_audio([0, 0]), segments=(segment,))
    assert result.status == "skipped"
    assert result.reason_code == "unavailable"
    assert result.segments == (segment,)
    assert result.segments[0] is segment


def test_diarization_manifest_tampering_is_unavailable_without_loading(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    config = model_path / "config.yaml"
    config.write_text("version: 1\n", encoding="utf-8")
    manifest = _diarization_manifest(model_path)
    config.write_text("version: tampered\n", encoding="utf-8")
    loads: list[Path] = []
    adapter = PyannoteDiarizationAdapter(
        manifest=manifest,
        allowed_model_roots=(tmp_path,),
        pipeline_loader=lambda path: loads.append(path) or _Pipeline(),
        tensor_builder=lambda audio: audio,
        offline_guard=lambda: None,
    )
    assert adapter.capability().available is False
    assert loads == []


def test_diarization_default_guard_denies_load_without_offline_runtime_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        monkeypatch.delenv(variable, raising=False)
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "config.yaml").write_text("version: 1\n", encoding="utf-8")
    adapter = PyannoteDiarizationAdapter(
        manifest=_diarization_manifest(model_path),
        allowed_model_roots=(tmp_path,),
        pipeline_loader=lambda _path: _Pipeline(),
        tensor_builder=lambda audio: audio,
    )
    assert adapter.capability().reason_code == "offline_guard_missing"


def _routing_policy() -> RoutingPolicyEnvelope:
    return RoutingPolicyEnvelope(
        allowed_backends=("fast", "deep", "cloud"),
        preferred_backends=("fast", "deep", "cloud"),
        allowed_devices=("cuda", "cpu"),
        max_candidate_count=2,
        max_total_latency_ms=100,
        max_regional_rerun_ms=600,
        confidence_threshold=0.7,
    )


def _routing_capabilities() -> tuple[BackendRoute, ...]:
    return (
        BackendRoute("fast", True, True, ("cpu",), 10, 1),
        BackendRoute("deep", True, True, ("cuda", "cpu"), 20, 2, supports_regional_input=True),
        BackendRoute("cloud", False, True, ("cpu",), 1, 1, supports_regional_input=True),
    )


def test_adaptive_router_is_deterministic_local_policy_bounded_and_regional() -> None:
    measurements = RoutingMeasurements(
        audio_duration_ms=1_000,
        overall_confidence=0.4,
        overall_calibration_id="cal-v1",
        confidence_regions=(
            ConfidenceRegion(100, 300, 0.3, "cal-v1"),
            ConfidenceRegion(250, 500, 0.4, "cal-v1"),
            ConfidenceRegion(700, 900, 0.2, None),
        ),
        available_devices=("cpu", "cuda"),
    )
    router = AdaptiveLocalRouter()

    first = router.decide(policy=_routing_policy(), measurements=measurements, capabilities=_routing_capabilities())
    second = router.decide(policy=_routing_policy(), measurements=measurements, capabilities=_routing_capabilities())

    assert first == second
    assert [item.backend_id for item in first.selected_backends] == ["fast", "deep"]
    assert first.rerun_regions[0].start_ms == 100
    assert first.rerun_regions[0].end_ms == 500
    assert all(item.start_ms != 700 for item in first.rerun_regions)
    assert ("cloud", "remote_execution_blocked") in {
        (item.backend_id, item.reason_code) for item in first.skipped_backends
    }
    assert first.remote_execution_allowed is False


def test_adaptive_router_does_not_escalate_on_uncalibrated_confidence() -> None:
    measurements = RoutingMeasurements(
        audio_duration_ms=1_000,
        overall_confidence=0.1,
        overall_calibration_id=None,
        confidence_regions=(ConfidenceRegion(100, 200, 0.1, None),),
        available_devices=("cpu", "cuda"),
    )
    decision = AdaptiveLocalRouter().decide(
        policy=_routing_policy(), measurements=measurements, capabilities=_routing_capabilities()
    )
    assert [item.backend_id for item in decision.selected_backends] == ["fast"]
    assert decision.rerun_regions == ()
    assert "uncalibrated_confidence_not_used" in decision.reason_codes


def test_adaptive_router_can_escalate_only_a_calibrated_region() -> None:
    measurements = RoutingMeasurements(
        audio_duration_ms=1_000,
        overall_confidence=0.95,
        overall_calibration_id="cal-v1",
        confidence_regions=(ConfidenceRegion(400, 550, 0.2, "cal-v1"),),
        available_devices=("cpu", "cuda"),
    )
    decision = AdaptiveLocalRouter().decide(
        policy=_routing_policy(), measurements=measurements, capabilities=_routing_capabilities()
    )
    assert [item.backend_id for item in decision.selected_backends] == ["fast", "deep"]
    assert [(item.start_ms, item.end_ms) for item in decision.rerun_regions] == [(400, 550)]


def test_regional_merge_preserves_unaffected_segment_instances() -> None:
    first = TranscriptionSegment(0, 100, "first")
    affected = TranscriptionSegment(100, 300, "old")
    last = TranscriptionSegment(300, 400, "last")
    region = RerunRegion("region-1", 100, 300, "deep", "cpu")
    replacement = TranscriptionSegment(100, 300, "new")

    merged = merge_regional_segments(
        baseline=(first, affected, last),
        regions=(region,),
        replacements={"region-1": (replacement,)},
    )
    assert merged == (first, replacement, last)
    assert merged[0] is first
    assert merged[2] is last
    failed_rerun = merge_regional_segments(
        baseline=(first, affected, last),
        regions=(region,),
        replacements={},
    )
    assert failed_rerun == (first, affected, last)
    with pytest.raises(ValueError):
        merge_regional_segments(
            baseline=(first, affected, last),
            regions=(region,),
            replacements={"region-1": (TranscriptionSegment(90, 300, "escape"),)},
        )


class _StrictExecutor:
    def __init__(self, response: Mapping[str, object] | Exception) -> None:
        self.response = response
        self.payload: Mapping[str, object] | None = None

    def execute(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.payload = payload
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _strict_request() -> StrictChoiceRequest:
    return StrictChoiceRequest(
        region_id="region-1",
        operation="classification",
        choices=(StrictChoice("base", "old text"), StrictChoice("alt", "new text")),
        baseline_choice_id="base",
    )


def test_strict_choice_judge_accepts_only_known_ids_and_no_generation() -> None:
    executor = _StrictExecutor(
        {
            "operation": "classification",
            "no_generation": True,
            "scores": {"base": 0.2, "alt": 0.8},
        }
    )
    outcome = StrictChoiceJudge(executor).evaluate(_strict_request())

    assert outcome.choice_id == "alt"
    assert outcome.text == "new text"
    assert outcome.no_generation is True
    assert executor.payload is not None
    assert executor.payload["no_generation"] is True
    assert "prompt" not in executor.payload


@pytest.mark.parametrize(
    "response",
    [
        {"operation": "classification", "no_generation": True, "choice_id": "unknown"},
        {
            "operation": "classification",
            "no_generation": True,
            "choice_id": "alt",
            "corrected_text": "free text",
        },
        {"operation": "classification", "no_generation": False, "choice_id": "alt"},
        {"operation": "classification", "no_generation": True, "scores": {"alt": "0.9"}},
        TimeoutError("timeout"),
    ],
)
def test_strict_choice_invalid_or_failed_response_is_exact_baseline(response: Mapping[str, object] | Exception) -> None:
    outcome = StrictChoiceJudge(_StrictExecutor(response)).evaluate(_strict_request())
    assert outcome.status == "fallback"
    assert outcome.choice_id == "base"
    assert outcome.text == "old text"
    assert outcome.scores == {}


class _LocalTransport:
    def __init__(self, response: Mapping[str, object] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        *,
        endpoint: str,
        payload: Mapping[str, object],
        timeout_ms: int,
        allow_redirects: bool,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "endpoint": endpoint,
                "payload": payload,
                "timeout_ms": timeout_ms,
                "allow_redirects": allow_redirects,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _generative_request() -> GenerativeJudgeRequest:
    return GenerativeJudgeRequest(
        region_id="region-1",
        baseline_text="baseline",
        candidate_texts=("baseline", "candidate"),
    )


def _generative_policy(*, enabled: bool = True) -> LocalGenerativeJudgePolicy:
    endpoint = "http://127.0.0.1:8080/judge"
    return LocalGenerativeJudgePolicy(
        enabled=enabled,
        endpoint=endpoint,
        allowlisted_endpoints=(endpoint,),
    )


def test_local_generative_judge_is_separate_opt_in_loopback_path_without_redirects() -> None:
    transport = _LocalTransport({"corrected_text": "candidate"})
    outcome = LocalGenerativeJudge(transport=transport).evaluate(
        request=_generative_request(),
        policy=_generative_policy(),
    )
    assert outcome.text == "candidate"
    assert outcome.execution_path == "local_generative"
    assert outcome.restricted_inference_result is False
    assert transport.calls[0]["allow_redirects"] is False


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        ({"corrected_text": "invented hallucination"}, "generative_output_unprovenanced"),
        ({"wrong_field": "candidate"}, "generative_judge_failed"),
        (TimeoutError("timeout"), "generative_judge_failed"),
    ],
)
def test_generative_judge_failure_or_hallucination_returns_consensus_baseline(
    response: Mapping[str, object] | Exception,
    expected_reason: str,
) -> None:
    outcome = LocalGenerativeJudge(transport=_LocalTransport(response)).evaluate(
        request=_generative_request(),
        policy=_generative_policy(),
    )
    assert outcome.text == "baseline"
    assert outcome.status == "fallback"
    assert outcome.reason_code == expected_reason


def test_generative_judge_disabled_does_not_call_transport() -> None:
    transport = _LocalTransport({"corrected_text": "candidate"})
    outcome = LocalGenerativeJudge(transport=transport).evaluate(
        request=_generative_request(),
        policy=_generative_policy(enabled=False),
    )
    assert outcome.text == "baseline"
    assert outcome.reason_code == "generative_judge_disabled"
    assert transport.calls == []


def test_loopback_endpoint_validation_blocks_dns_remote_and_non_allowlisted_targets() -> None:
    allowed = ("http://127.0.0.1:8080/judge",)
    assert validate_loopback_endpoint(allowed[0], allowed) == allowed[0]
    with pytest.raises(ValueError, match="DNS"):
        validate_loopback_endpoint("http://localhost:8080/judge", ("http://localhost:8080/judge",))
    with pytest.raises(ValueError, match="loopback"):
        validate_loopback_endpoint("https://8.8.8.8:443/judge", ("https://8.8.8.8:443/judge",))
    with pytest.raises(ValueError, match="allowlisted"):
        validate_loopback_endpoint("http://127.0.0.1:8081/judge", allowed)
