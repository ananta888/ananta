from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import struct
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol, Sequence

from voice_runtime.errors import BackendModelError, BackendUnavailableError, PolicyBlockedError

from .audio_decode import DecodedPcmAudio


@dataclass(frozen=True)
class AudioQualityMetrics:
    dc_offset: float
    peak: float
    rms: float
    clipped_fraction: float
    silence_fraction: float
    frame_count: int
    sample_rate_hz: int
    channels: int

    def as_dict(self) -> dict[str, int | float]:
        return {
            "dc_offset": self.dc_offset,
            "peak": self.peak,
            "rms": self.rms,
            "clipped_fraction": self.clipped_fraction,
            "silence_fraction": self.silence_fraction,
            "frame_count": self.frame_count,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
        }


@dataclass(frozen=True)
class TimeAxisMapping:
    source_start_ms: int
    source_end_ms: int
    variant_start_ms: int
    variant_end_ms: int

    def variant_to_source_ms(self, timestamp_ms: int) -> int:
        variant_duration = self.variant_end_ms - self.variant_start_ms
        source_duration = self.source_end_ms - self.source_start_ms
        if variant_duration <= 0:
            return self.source_start_ms
        bounded = max(self.variant_start_ms, min(int(timestamp_ms), self.variant_end_ms))
        relative = bounded - self.variant_start_ms
        return self.source_start_ms + round(relative * source_duration / variant_duration)

    def as_dict(self) -> dict[str, int]:
        return {
            "source_start_ms": self.source_start_ms,
            "source_end_ms": self.source_end_ms,
            "variant_start_ms": self.variant_start_ms,
            "variant_end_ms": self.variant_end_ms,
        }


@dataclass(frozen=True)
class EnhancementStep:
    processor_id: str
    processor_version: str
    parameters: Mapping[str, object]
    input_pcm_sha256: str
    output_pcm_sha256: str
    quality_before: AudioQualityMetrics
    quality_after: AudioQualityMetrics
    runtime_ms: float

    def stable_dict(self) -> dict[str, object]:
        return {
            "processor_id": self.processor_id,
            "processor_version": self.processor_version,
            "parameters": dict(self.parameters),
            "input_pcm_sha256": self.input_pcm_sha256,
            "output_pcm_sha256": self.output_pcm_sha256,
            "quality_before": self.quality_before.as_dict(),
            "quality_after": self.quality_after.as_dict(),
        }

    def public_dict(self) -> dict[str, object]:
        """Return transformation provenance without an audio fingerprint."""

        return {
            "processor_id": self.processor_id,
            "processor_version": self.processor_version,
            "parameters": dict(self.parameters),
            "runtime_ms": self.runtime_ms,
        }


@dataclass(frozen=True)
class EnhancementVariant:
    variant_id: str
    label: str
    audio: DecodedPcmAudio
    parent_variant_id: str | None
    lineage: tuple[str, ...]
    steps: tuple[EnhancementStep, ...]
    quality: AudioQualityMetrics
    time_axis: TimeAxisMapping
    runtime_ms: float

    def as_metadata(self) -> dict[str, object]:
        return {
            "variant_id": self.variant_id,
            "label": self.label,
            "parent_variant_id": self.parent_variant_id,
            "lineage": list(self.lineage),
            "steps": [
                step.public_dict()
                for step in self.steps
            ],
            "time_axis": self.time_axis.as_dict(),
            "runtime_ms": self.runtime_ms,
        }


class AudioEnhancementProcessor(Protocol):
    @property
    def processor_id(self) -> str: ...

    @property
    def processor_version(self) -> str: ...

    def parameters(self) -> Mapping[str, object]: ...

    def analyze(self, audio: DecodedPcmAudio) -> AudioQualityMetrics: ...

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio: ...


class PcmEnhancementProcessor:
    processor_id = "pcm"
    processor_version = "1"

    def parameters(self) -> Mapping[str, object]:
        return {}

    def analyze(self, audio: DecodedPcmAudio) -> AudioQualityMetrics:
        return analyze_pcm_quality(audio)


class BypassProcessor(PcmEnhancementProcessor):
    processor_id = "bypass"

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        return audio


class DcOffsetRemovalProcessor(PcmEnhancementProcessor):
    processor_id = "dc_offset_removal"

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        samples = _decode_samples(audio)
        if not samples:
            return audio
        channels = audio.channels
        offsets = [
            round(sum(samples[channel::channels]) / len(samples[channel::channels])) for channel in range(channels)
        ]
        transformed = [_clamp_s16(sample - offsets[index % channels]) for index, sample in enumerate(samples)]
        return _replace_pcm(audio, transformed)


@dataclass(frozen=True)
class PeakNormalizationProcessor(PcmEnhancementProcessor):
    target_peak: float = 0.95
    max_gain: float = 8.0
    processor_id: str = field(default="peak_normalization", init=False)
    processor_version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        if not 0 < self.target_peak <= 1:
            raise ValueError("target_peak must be in (0, 1]")
        if self.max_gain < 1:
            raise ValueError("max_gain must be at least 1")

    def parameters(self) -> Mapping[str, object]:
        return {"target_peak": self.target_peak, "max_gain": self.max_gain}

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        samples = _decode_samples(audio)
        peak = max((abs(sample) for sample in samples), default=0)
        if peak == 0:
            return audio
        target = round(self.target_peak * 32767)
        gain = min(self.max_gain, target / peak)
        return _replace_pcm(audio, [_clamp_s16(round(sample * gain)) for sample in samples])


@dataclass(frozen=True)
class HighPassProcessor(PcmEnhancementProcessor):
    cutoff_hz: float = 80.0
    processor_id: str = field(default="high_pass", init=False)
    processor_version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        if self.cutoff_hz <= 0:
            raise ValueError("cutoff_hz must be positive")

    def parameters(self) -> Mapping[str, object]:
        return {"cutoff_hz": self.cutoff_hz}

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        samples = _decode_samples(audio)
        if not samples:
            return audio
        channels = audio.channels
        time_step = 1.0 / audio.sample_rate_hz
        rc = 1.0 / (2.0 * math.pi * self.cutoff_hz)
        alpha = rc / (rc + time_step)
        previous_input = [0.0] * channels
        previous_output = [0.0] * channels
        transformed: list[int] = []
        for index, sample in enumerate(samples):
            channel = index % channels
            output = alpha * (previous_output[channel] + sample - previous_input[channel])
            previous_input[channel] = sample
            previous_output[channel] = output
            transformed.append(_clamp_s16(round(output)))
        return _replace_pcm(audio, transformed)


@dataclass(frozen=True)
class LimiterProcessor(PcmEnhancementProcessor):
    threshold: float = 0.98
    processor_id: str = field(default="limiter", init=False)
    processor_version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        if not 0 < self.threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")

    def parameters(self) -> Mapping[str, object]:
        return {"threshold": self.threshold}

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        limit = round(self.threshold * 32767)
        return _replace_pcm(audio, [max(-limit, min(limit, sample)) for sample in _decode_samples(audio)])


class ChannelMixProcessor(PcmEnhancementProcessor):
    processor_id = "channel_mix_mono"

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        if audio.channels == 1:
            return audio
        samples = _decode_samples(audio)
        mono = [
            _clamp_s16(round(sum(samples[index : index + audio.channels]) / audio.channels))
            for index in range(0, len(samples), audio.channels)
        ]
        return replace(audio, pcm_s16le=_encode_samples(mono), channels=1, sample_width_bytes=2)


@dataclass(frozen=True)
class ResampleProcessor(PcmEnhancementProcessor):
    target_sample_rate_hz: int = 16_000
    processor_id: str = field(default="linear_resample", init=False)
    processor_version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        if self.target_sample_rate_hz <= 0:
            raise ValueError("target_sample_rate_hz must be positive")

    def parameters(self) -> Mapping[str, object]:
        return {"target_sample_rate_hz": self.target_sample_rate_hz}

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        if audio.sample_rate_hz == self.target_sample_rate_hz:
            return audio
        samples = _decode_samples(audio)
        source_frames = len(samples) // audio.channels
        if source_frames == 0:
            return replace(audio, sample_rate_hz=self.target_sample_rate_hz, duration_ms=0)
        target_frames = max(1, round(source_frames * self.target_sample_rate_hz / audio.sample_rate_hz))
        transformed: list[int] = []
        for target_index in range(target_frames):
            source_position = target_index * audio.sample_rate_hz / self.target_sample_rate_hz
            left = min(int(source_position), source_frames - 1)
            right = min(left + 1, source_frames - 1)
            fraction = source_position - left
            for channel in range(audio.channels):
                left_sample = samples[left * audio.channels + channel]
                right_sample = samples[right * audio.channels + channel]
                transformed.append(_clamp_s16(round(left_sample + (right_sample - left_sample) * fraction)))
        duration_ms = round(target_frames * 1000 / self.target_sample_rate_hz)
        return replace(
            audio,
            pcm_s16le=_encode_samples(transformed),
            sample_rate_hz=self.target_sample_rate_hz,
            duration_ms=duration_ms,
            sample_width_bytes=2,
        )


class DeterministicAudioEnhancementPipeline:
    """Runs explicit processor chains without selecting a variant automatically."""

    def __init__(self, *, lineage_nonce: str | None = None) -> None:
        self._lineage_nonce = lineage_nonce or uuid.uuid4().hex

    def original_variant(self, audio: DecodedPcmAudio) -> EnhancementVariant:
        quality = analyze_pcm_quality(audio)
        variant_id = _variant_id(
            lineage_nonce=self._lineage_nonce,
            label="original",
            steps=(),
            parent_variant_id=None,
        )
        return EnhancementVariant(
            variant_id=variant_id,
            label="original",
            audio=audio,
            parent_variant_id=None,
            lineage=(),
            steps=(),
            quality=quality,
            time_axis=_time_axis(audio, audio),
            runtime_ms=0.0,
        )

    def run(
        self,
        source: EnhancementVariant,
        processors: Sequence[AudioEnhancementProcessor],
        *,
        label: str,
    ) -> EnhancementVariant:
        if not label.strip():
            raise ValueError("enhancement variant label must not be empty")
        started = time.perf_counter()
        current = source.audio
        steps: list[EnhancementStep] = []
        for processor in processors:
            before = processor.analyze(current)
            step_started = time.perf_counter()
            transformed = processor.transform(current)
            runtime_ms = (time.perf_counter() - step_started) * 1000
            _validate_transformed_audio(current, transformed)
            after = processor.analyze(transformed)
            steps.append(
                EnhancementStep(
                    processor_id=processor.processor_id,
                    processor_version=processor.processor_version,
                    parameters=_canonical_parameters(processor.parameters()),
                    input_pcm_sha256=_pcm_sha256(current),
                    output_pcm_sha256=_pcm_sha256(transformed),
                    quality_before=before,
                    quality_after=after,
                    runtime_ms=round(runtime_ms, 6),
                )
            )
            current = transformed
        total_runtime = (time.perf_counter() - started) * 1000
        variant_id = _variant_id(
            lineage_nonce=self._lineage_nonce,
            label=label,
            steps=tuple(steps),
            parent_variant_id=source.variant_id,
        )
        return EnhancementVariant(
            variant_id=variant_id,
            label=label,
            audio=current,
            parent_variant_id=source.variant_id,
            lineage=(*source.lineage, source.variant_id),
            steps=tuple(steps),
            quality=analyze_pcm_quality(current),
            time_axis=TimeAxisMapping(
                source_start_ms=source.time_axis.source_start_ms,
                source_end_ms=source.time_axis.source_end_ms,
                variant_start_ms=current.timeline_start_ms,
                variant_end_ms=current.timeline_end_ms,
            ),
            runtime_ms=round(total_runtime, 6),
        )


@dataclass(frozen=True)
class OptionalEnhancementCapability:
    adapter_id: str
    version: str
    available: bool
    reason_code: str | None
    local_only: bool = True
    downloads_allowed: bool = False


@dataclass(frozen=True)
class EnhancementBenchmarkResult:
    baseline_wer: float
    variant_wer: float
    wer_delta: float
    baseline_cer: float
    variant_cer: float
    cer_delta: float

    @property
    def degraded(self) -> bool:
        return self.wer_delta > 0 or self.cer_delta > 0


@dataclass(frozen=True)
class LocalEnhancementEntrypoint:
    adapter_id: str
    module_name: str
    callable_name: str = "enhance_pcm_s16le"
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.adapter_id or not self.module_name or not self.callable_name:
            raise ValueError("local enhancement entrypoint fields must not be empty")
        if any(token in self.module_name for token in ("/", "\\", "..")):
            raise ValueError("local enhancement module name is invalid")


LocalEnhancementCallable = Callable[[bytes, int, int, Mapping[str, object]], bytes]
EntrypointLoader = Callable[[LocalEnhancementEntrypoint], LocalEnhancementCallable]


class LazyLocalEnhancementProcessor:
    """Lazy ABI for explicitly configured in-process, local-only enhancers.

    Plugins receive PCM bytes, sample rate, channel count and data-only
    parameters. They must return PCM with exactly the same shape. Model paths
    belong in parameters and are never downloaded by this adapter.
    """

    def __init__(
        self,
        *,
        entrypoint: LocalEnhancementEntrypoint,
        parameters: Mapping[str, object] | None = None,
        loader: EntrypointLoader | None = None,
        downloads_allowed: bool = False,
    ) -> None:
        if downloads_allowed:
            raise PolicyBlockedError("local enhancement adapters cannot enable downloads")
        self._entrypoint = entrypoint
        self._parameters = _canonical_parameters(parameters or {})
        _validate_local_adapter_parameters(self._parameters)
        self._parameters["downloads_allowed"] = False
        self._loader = loader
        self._callable: LocalEnhancementCallable | None = None

    @property
    def processor_id(self) -> str:
        return f"optional:{self._entrypoint.adapter_id}"

    @property
    def processor_version(self) -> str:
        return self._entrypoint.version

    def parameters(self) -> Mapping[str, object]:
        return dict(self._parameters)

    def analyze(self, audio: DecodedPcmAudio) -> AudioQualityMetrics:
        return analyze_pcm_quality(audio)

    def capability(self) -> OptionalEnhancementCapability:
        if self._loader is not None:
            available = True
        else:
            try:
                available = importlib.util.find_spec(self._entrypoint.module_name) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                available = False
        return OptionalEnhancementCapability(
            adapter_id=self._entrypoint.adapter_id,
            version=self._entrypoint.version,
            available=available,
            reason_code=None if available else "dependency_unavailable",
        )

    def transform(self, audio: DecodedPcmAudio) -> DecodedPcmAudio:
        enhancer = self._load()
        try:
            output = enhancer(
                audio.pcm_s16le,
                audio.sample_rate_hz,
                audio.channels,
                _canonical_parameters(self._parameters),
            )
        except Exception as exc:
            raise BackendModelError("local enhancement adapter failed") from exc
        if not isinstance(output, bytes) or len(output) != len(audio.pcm_s16le):
            raise BackendModelError("local enhancement adapter returned an invalid PCM shape")
        return replace(audio, pcm_s16le=output)

    def _load(self) -> LocalEnhancementCallable:
        if self._callable is not None:
            return self._callable
        if not self.capability().available:
            raise BackendUnavailableError("local enhancement dependency is unavailable")
        try:
            if self._loader is not None:
                loaded = self._loader(self._entrypoint)
            else:
                module = importlib.import_module(self._entrypoint.module_name)
                loaded = getattr(module, self._entrypoint.callable_name)
        except (AttributeError, ImportError, ModuleNotFoundError) as exc:
            raise BackendUnavailableError("local enhancement entrypoint is unavailable") from exc
        if not callable(loaded):
            raise BackendUnavailableError("local enhancement entrypoint is unavailable")
        self._callable = loaded
        return loaded


def analyze_pcm_quality(audio: DecodedPcmAudio) -> AudioQualityMetrics:
    samples = _decode_samples(audio)
    if not samples:
        return AudioQualityMetrics(
            dc_offset=0.0,
            peak=0.0,
            rms=0.0,
            clipped_fraction=0.0,
            silence_fraction=1.0,
            frame_count=0,
            sample_rate_hz=audio.sample_rate_hz,
            channels=audio.channels,
        )
    count = len(samples)
    return AudioQualityMetrics(
        dc_offset=_quantize(sum(samples) / count / 32768),
        peak=_quantize(max(abs(sample) for sample in samples) / 32768),
        rms=_quantize(math.sqrt(sum(sample * sample for sample in samples) / count) / 32768),
        clipped_fraction=_quantize(sum(abs(sample) >= 32767 for sample in samples) / count),
        silence_fraction=_quantize(sum(abs(sample) <= 64 for sample in samples) / count),
        frame_count=count // audio.channels,
        sample_rate_hz=audio.sample_rate_hz,
        channels=audio.channels,
    )


def compare_transcript_error_rates(
    *,
    reference: str,
    baseline_text: str,
    variant_text: str,
) -> EnhancementBenchmarkResult:
    """Expose quality regressions without turning them into auto-routing policy."""

    reference_words = tuple(reference.casefold().split())
    baseline_words = tuple(baseline_text.casefold().split())
    variant_words = tuple(variant_text.casefold().split())
    reference_chars = tuple(character for character in reference.casefold() if not character.isspace())
    baseline_chars = tuple(character for character in baseline_text.casefold() if not character.isspace())
    variant_chars = tuple(character for character in variant_text.casefold() if not character.isspace())
    baseline_wer = _error_rate(reference_words, baseline_words)
    variant_wer = _error_rate(reference_words, variant_words)
    baseline_cer = _error_rate(reference_chars, baseline_chars)
    variant_cer = _error_rate(reference_chars, variant_chars)
    return EnhancementBenchmarkResult(
        baseline_wer=baseline_wer,
        variant_wer=variant_wer,
        wer_delta=_quantize(variant_wer - baseline_wer),
        baseline_cer=baseline_cer,
        variant_cer=variant_cer,
        cer_delta=_quantize(variant_cer - baseline_cer),
    )


def _decode_samples(audio: DecodedPcmAudio) -> list[int]:
    if audio.sample_width_bytes != 2:
        raise ValueError("audio enhancement accepts signed 16-bit PCM only")
    if audio.channels <= 0 or len(audio.pcm_s16le) % (audio.channels * 2):
        raise ValueError("audio enhancement received malformed interleaved PCM")
    if not audio.pcm_s16le:
        return []
    count = len(audio.pcm_s16le) // 2
    return list(struct.unpack(f"<{count}h", audio.pcm_s16le))


def _encode_samples(samples: Sequence[int]) -> bytes:
    if not samples:
        return b""
    return struct.pack(f"<{len(samples)}h", *samples)


def _replace_pcm(audio: DecodedPcmAudio, samples: Sequence[int]) -> DecodedPcmAudio:
    return replace(audio, pcm_s16le=_encode_samples(samples), sample_width_bytes=2)


def _clamp_s16(value: int) -> int:
    return max(-32768, min(32767, int(value)))


def _quantize(value: float) -> float:
    return round(float(value), 8)


def _canonical_parameters(parameters: Mapping[str, object]) -> dict[str, object]:
    try:
        canonical = json.loads(json.dumps(dict(parameters), sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError) as exc:
        raise ValueError("enhancement parameters must be JSON serializable") from exc
    if not isinstance(canonical, dict):
        raise ValueError("enhancement parameters must be an object")
    return canonical


def _pcm_sha256(audio: DecodedPcmAudio) -> str:
    digest = hashlib.sha256()
    digest.update(audio.pcm_s16le)
    digest.update(f"|{audio.sample_rate_hz}|{audio.channels}|{audio.timeline_start_ms}".encode())
    return digest.hexdigest()


def _variant_id(
    *,
    lineage_nonce: str,
    label: str,
    steps: tuple[EnhancementStep, ...],
    parent_variant_id: str | None,
) -> str:
    payload: dict[str, Any] = {
        "label": label,
        "lineage_nonce": lineage_nonce,
        "parent_variant_id": parent_variant_id,
        "steps": [
            {
                "processor_id": step.processor_id,
                "processor_version": step.processor_version,
                "parameters": dict(step.parameters),
            }
            for step in steps
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"audio-{hashlib.sha256(serialized).hexdigest()[:24]}"


def _time_axis(source: DecodedPcmAudio, variant: DecodedPcmAudio) -> TimeAxisMapping:
    return TimeAxisMapping(
        source_start_ms=source.timeline_start_ms,
        source_end_ms=source.timeline_end_ms,
        variant_start_ms=variant.timeline_start_ms,
        variant_end_ms=variant.timeline_end_ms,
    )


def _validate_transformed_audio(source: DecodedPcmAudio, transformed: DecodedPcmAudio) -> None:
    if not isinstance(transformed, DecodedPcmAudio):
        raise TypeError("enhancement processor must return DecodedPcmAudio")
    if transformed.timeline_start_ms != source.timeline_start_ms:
        raise ValueError("enhancement processor must preserve the absolute timeline start")
    if transformed.sample_width_bytes != 2:
        raise ValueError("enhancement processor must return signed 16-bit PCM")
    _decode_samples(transformed)


def _validate_local_adapter_parameters(parameters: Mapping[str, object]) -> None:
    for key, value in parameters.items():
        normalized_key = str(key).casefold()
        if "download" in normalized_key and value not in (False, None, 0, ""):
            raise PolicyBlockedError("local enhancement parameters cannot enable downloads")
        if isinstance(value, str) and "://" in value:
            raise PolicyBlockedError("local enhancement parameters cannot contain remote URLs")
        if isinstance(value, Mapping):
            _validate_local_adapter_parameters(value)


def _error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_item in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_item in enumerate(hypothesis, start=1):
            substitution = previous[hypothesis_index - 1] + (reference_item != hypothesis_item)
            current.append(min(previous[hypothesis_index] + 1, current[-1] + 1, substitution))
        previous = current
    return _quantize(previous[-1] / max(1, len(reference)))
