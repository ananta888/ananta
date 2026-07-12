from __future__ import annotations

import importlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from .audio_decode import DecodedPcmAudio


@dataclass(frozen=True)
class VadSettings:
    frame_ms: int = 30
    aggressiveness: int = 2
    padding_ms: int = 150
    min_speech_ms: int = 90
    min_silence_ms: int = 180
    max_segment_ms: int = 30_000

    def __post_init__(self) -> None:
        if self.frame_ms not in {10, 20, 30}:
            raise ValueError("VAD frame_ms must be 10, 20, or 30")
        if self.aggressiveness not in {0, 1, 2, 3}:
            raise ValueError("VAD aggressiveness must be between 0 and 3")
        if self.padding_ms < 0 or self.min_speech_ms <= 0 or self.min_silence_ms <= 0:
            raise ValueError("VAD timing settings are invalid")
        if self.max_segment_ms < self.min_speech_ms:
            raise ValueError("VAD max_segment_ms must be at least min_speech_ms")


@dataclass(frozen=True)
class PcmVadSegment:
    audio: DecodedPcmAudio
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("invalid VAD segment timeline")


class PcmVadProcessor(Protocol):
    def name(self) -> str: ...

    def split(self, audio: DecodedPcmAudio) -> tuple[PcmVadSegment, ...]: ...


class PassThroughPcmVad:
    def name(self) -> str:
        return "passthrough"

    def split(self, audio: DecodedPcmAudio) -> tuple[PcmVadSegment, ...]:
        if not audio.pcm_s16le:
            return ()
        return (
            PcmVadSegment(
                audio=audio,
                start_ms=audio.timeline_start_ms,
                end_ms=audio.timeline_end_ms,
            ),
        )


class _WebRtcVad(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class WebRtcPcmVad:
    _SUPPORTED_SAMPLE_RATES = {8_000, 16_000, 32_000, 48_000}

    def __init__(
        self,
        *,
        settings: VadSettings | None = None,
        vad_factory: Callable[[int], _WebRtcVad] | None = None,
    ) -> None:
        self._settings = settings or VadSettings()
        self._vad_factory = vad_factory

    def name(self) -> str:
        return "webrtcvad"

    def split(self, audio: DecodedPcmAudio) -> tuple[PcmVadSegment, ...]:
        if audio.channels != 1 or audio.sample_width_bytes != 2:
            raise ValueError("webrtcvad requires mono 16-bit PCM")
        if audio.sample_rate_hz not in self._SUPPORTED_SAMPLE_RATES:
            raise ValueError("webrtcvad sample rate is unsupported")
        if not audio.pcm_s16le:
            return ()

        detector = self._build_detector()
        bytes_per_frame = audio.sample_rate_hz * self._settings.frame_ms // 1000 * 2
        decisions: list[bool] = []
        for offset in range(0, len(audio.pcm_s16le), bytes_per_frame):
            frame = audio.pcm_s16le[offset : offset + bytes_per_frame]
            if len(frame) < bytes_per_frame:
                frame = frame + (b"\x00" * (bytes_per_frame - len(frame)))
            decisions.append(bool(detector.is_speech(frame, audio.sample_rate_hz)))
        ranges = self._speech_ranges(decisions=decisions, duration_ms=audio.duration_ms)
        return tuple(self._segment(audio=audio, start_ms=start, end_ms=end) for start, end in ranges)

    def _build_detector(self) -> _WebRtcVad:
        if self._vad_factory is not None:
            return self._vad_factory(self._settings.aggressiveness)
        try:
            module = importlib.import_module("webrtcvad")
        except Exception as exc:
            raise RuntimeError("webrtcvad backend unavailable: optional dependency is not installed") from exc
        return cast(_WebRtcVad, module.Vad(self._settings.aggressiveness))

    def _speech_ranges(self, *, decisions: list[bool], duration_ms: int) -> list[tuple[int, int]]:
        frame_ms = self._settings.frame_ms
        ranges: list[tuple[int, int]] = []
        speech_start: int | None = None
        last_speech_end = 0
        silence_ms = 0
        for index, speech in enumerate(decisions):
            frame_start = index * frame_ms
            frame_end = min(duration_ms, frame_start + frame_ms)
            if speech:
                if speech_start is None:
                    speech_start = frame_start
                last_speech_end = frame_end
                silence_ms = 0
            elif speech_start is not None:
                silence_ms += frame_ms

            if speech_start is not None and last_speech_end - speech_start >= self._settings.max_segment_ms:
                self._append_range(
                    ranges,
                    speech_start=speech_start,
                    speech_end=last_speech_end,
                    duration_ms=duration_ms,
                )
                speech_start = None
                silence_ms = 0
            elif speech_start is not None and silence_ms >= self._settings.min_silence_ms:
                self._append_range(
                    ranges,
                    speech_start=speech_start,
                    speech_end=last_speech_end,
                    duration_ms=duration_ms,
                )
                speech_start = None
                silence_ms = 0

        if speech_start is not None:
            self._append_range(ranges, speech_start=speech_start, speech_end=last_speech_end, duration_ms=duration_ms)
        return self._merge_overlapping_ranges(ranges)

    def _append_range(
        self,
        ranges: list[tuple[int, int]],
        *,
        speech_start: int,
        speech_end: int,
        duration_ms: int,
    ) -> None:
        if speech_end - speech_start < self._settings.min_speech_ms:
            return
        start = max(0, speech_start - self._settings.padding_ms)
        end = min(duration_ms, speech_end + self._settings.padding_ms)
        cursor = start
        while end - cursor > self._settings.max_segment_ms:
            ranges.append((cursor, cursor + self._settings.max_segment_ms))
            cursor += self._settings.max_segment_ms
        if end > cursor:
            ranges.append((cursor, end))

    def _merge_overlapping_ranges(self, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if merged and start < merged[-1][1]:
                previous_start, previous_end = merged[-1]
                merged[-1] = (previous_start, max(previous_end, end))
            else:
                merged.append((start, end))
        bounded: list[tuple[int, int]] = []
        for start, end in merged:
            cursor = start
            while end - cursor > self._settings.max_segment_ms:
                bounded.append((cursor, cursor + self._settings.max_segment_ms))
                cursor += self._settings.max_segment_ms
            if end > cursor:
                bounded.append((cursor, end))
        return bounded

    @staticmethod
    def _segment(*, audio: DecodedPcmAudio, start_ms: int, end_ms: int) -> PcmVadSegment:
        sliced = audio.slice_ms(start_ms, end_ms)
        return PcmVadSegment(audio=sliced, start_ms=sliced.timeline_start_ms, end_ms=sliced.timeline_end_ms)


class SileroProbabilityProvider(Protocol):
    def probabilities(self, audio: DecodedPcmAudio, *, frame_samples: int) -> list[float]: ...


class LocalSileroProbabilityProvider:
    """Lazy, local-only TorchScript loader; it never uses torch.hub or downloads."""

    def __init__(self, model_path: str | None = None) -> None:
        self._model_path = model_path
        self._model: Any | None = None
        self._load_lock = threading.Lock()

    def probabilities(self, audio: DecodedPcmAudio, *, frame_samples: int) -> list[float]:
        model = self._load_model()
        try:
            torch = importlib.import_module("torch")
        except Exception as exc:
            raise RuntimeError("silero backend unavailable: optional dependency is not installed") from exc
        samples = torch.frombuffer(bytearray(audio.pcm_s16le), dtype=torch.int16).to(torch.float32) / 32768.0
        probabilities: list[float] = []
        if hasattr(model, "reset_states"):
            model.reset_states()
        with torch.inference_mode():
            for offset in range(0, len(samples), frame_samples):
                frame = samples[offset : offset + frame_samples]
                if len(frame) < frame_samples:
                    frame = torch.nn.functional.pad(frame, (0, frame_samples - len(frame)))
                value = model(frame, audio.sample_rate_hz)
                probabilities.append(max(0.0, min(1.0, float(value.item()))))
        return probabilities

    def _load_model(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            configured = str(self._model_path or os.getenv("VOICE_SILERO_VAD_MODEL_PATH") or "").strip()
            if not configured:
                raise RuntimeError("silero backend unavailable: local model path is not configured")
            unresolved = Path(configured).expanduser()
            try:
                path = unresolved.resolve(strict=True)
            except OSError as exc:
                raise RuntimeError("silero backend unavailable: local model file was not found") from exc
            if (
                unresolved.is_symlink()
                or not path.is_file()
                or path.suffix != ".jit"
                or path.stat().st_nlink != 1
                or not os.access(path, os.R_OK)
            ):
                raise RuntimeError("silero backend unavailable: local TorchScript model is invalid")
            try:
                torch = importlib.import_module("torch")
                model = torch.jit.load(str(path), map_location="cpu")
                model.eval()
                self._model = model
            except Exception as exc:
                raise RuntimeError("silero backend unavailable: local model could not be loaded") from exc
            return self._model


class SileroPcmVad:
    def __init__(
        self,
        *,
        provider: SileroProbabilityProvider | None = None,
        settings: VadSettings | None = None,
        threshold: float = 0.5,
    ) -> None:
        self._provider = provider or LocalSileroProbabilityProvider()
        self._settings = settings or VadSettings(frame_ms=30)
        self._threshold = max(0.0, min(1.0, float(threshold)))

    def name(self) -> str:
        return "silero"

    def split(self, audio: DecodedPcmAudio) -> tuple[PcmVadSegment, ...]:
        if audio.channels != 1 or audio.sample_width_bytes != 2 or audio.sample_rate_hz != 16_000:
            raise ValueError("silero VAD requires 16kHz mono 16-bit PCM")
        frame_samples = 512
        probabilities = self._provider.probabilities(audio, frame_samples=frame_samples)
        frame_ms = frame_samples * 1000 // audio.sample_rate_hz
        ranges = _decision_ranges(
            decisions=[value >= self._threshold for value in probabilities],
            duration_ms=audio.duration_ms,
            frame_ms=frame_ms,
            settings=self._settings,
        )
        return tuple(WebRtcPcmVad._segment(audio=audio, start_ms=start, end_ms=end) for start, end in ranges)


def build_pcm_vad_processor(
    backend: str,
    *,
    settings: VadSettings | None = None,
    silero_model_path: str | None = None,
    silero_threshold: float = 0.5,
) -> PcmVadProcessor:
    normalized = str(backend or "passthrough").strip().lower()
    if normalized in {"", "none", "mock", "passthrough"}:
        return PassThroughPcmVad()
    if normalized == "webrtcvad":
        return WebRtcPcmVad(settings=settings)
    if normalized == "silero":
        return SileroPcmVad(
            provider=LocalSileroProbabilityProvider(silero_model_path),
            settings=settings,
            threshold=silero_threshold,
        )
    raise ValueError(f"unsupported PCM VAD backend: {normalized}")


def _decision_ranges(
    *, decisions: list[bool], duration_ms: int, frame_ms: int, settings: VadSettings
) -> list[tuple[int, int]]:
    # Use the same deterministic segmentation policy as WebRTC. The temporary
    # adapter supplies decisions only and has no dependency on WebRTC itself.
    adapter = WebRtcPcmVad(settings=settings, vad_factory=lambda _level: _UnusedVad())
    original_frame_ms = adapter._settings.frame_ms
    if frame_ms == original_frame_ms:
        return adapter._speech_ranges(decisions=decisions, duration_ms=duration_ms)
    scaled = VadSettings(
        frame_ms=min({10, 20, 30}, key=lambda value: abs(value - frame_ms)),
        aggressiveness=settings.aggressiveness,
        padding_ms=settings.padding_ms,
        min_speech_ms=settings.min_speech_ms,
        min_silence_ms=settings.min_silence_ms,
        max_segment_ms=settings.max_segment_ms,
    )
    return WebRtcPcmVad(settings=scaled, vad_factory=lambda _level: _UnusedVad())._speech_ranges(
        decisions=decisions,
        duration_ms=duration_ms,
    )


class _UnusedVad:
    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        raise AssertionError("decision range helper does not inspect PCM frames")
