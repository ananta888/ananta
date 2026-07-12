from __future__ import annotations

import importlib
import importlib.util
import threading
from pathlib import Path
from typing import Any, Callable, cast

from ..execution_control import BackendCancellationToken
from ..preprocessing.audio_decode import AudioDecodeLimits, AudioDecoder, SafeAudioDecoder
from ..preprocessing.temp_workspace import temporary_audio_workspace
from .base import ChatResult, TranscriptionResult, TranscriptionSegment, TranscriptionWord, VoiceBackend


class FasterWhisperBackend(VoiceBackend):
    """Local-only Faster-Whisper adapter; model downloads are never implicit."""

    _DEVICES = {"auto", "cpu", "cuda"}
    _COMPUTE_TYPES = {
        "default",
        "auto",
        "int8",
        "int8_float16",
        "int8_float32",
        "int16",
        "float16",
        "float32",
        "bfloat16",
    }

    def __init__(
        self,
        *,
        model_path: str | None,
        model: str = "faster_whisper",
        device: str = "auto",
        compute_type: str = "default",
        beam_size: int = 5,
        vad_filter: bool = False,
        vad_min_silence_ms: int = 500,
        decoder: AudioDecoder | None = None,
        model_factory: Callable[..., Any] | None = None,
        allow_download: bool = False,
    ) -> None:
        normalized_device = str(device).strip().lower()
        normalized_compute_type = str(compute_type).strip().lower()
        if normalized_device not in self._DEVICES:
            raise ValueError(f"unsupported Faster-Whisper device: {normalized_device}")
        if normalized_compute_type not in self._COMPUTE_TYPES:
            raise ValueError(f"unsupported Faster-Whisper compute type: {normalized_compute_type}")
        if beam_size < 1 or beam_size > 20:
            raise ValueError("Faster-Whisper beam_size must be between 1 and 20")
        if vad_min_silence_ms < 0 or vad_min_silence_ms > 10_000:
            raise ValueError("Faster-Whisper vad_min_silence_ms must be between 0 and 10000")
        self._model_path = model_path
        self._model = model
        self._device = normalized_device
        self._compute_type = normalized_compute_type
        self._beam_size = beam_size
        self._vad_filter = bool(vad_filter)
        self._vad_min_silence_ms = int(vad_min_silence_ms)
        self._decode_limits = AudioDecodeLimits()
        self._decoder = decoder or SafeAudioDecoder(limits=self._decode_limits)
        self._model_factory = model_factory
        self._allow_download = bool(allow_download)
        self._loaded_model: Any | None = None
        self._load_lock = threading.Lock()

    def name(self) -> str:
        return "faster_whisper"

    def _resolved_model_path(self) -> Path:
        if not self._model_path:
            raise RuntimeError("Faster-Whisper backend unavailable: local model path is not configured")
        try:
            path = Path(self._model_path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("Faster-Whisper backend unavailable: local model path does not exist") from exc
        if not path.is_dir():
            raise RuntimeError("Faster-Whisper backend unavailable: local model path is not a directory")
        return path

    def _factory(self) -> Callable[..., Any]:
        if self._model_factory is not None:
            return self._model_factory
        try:
            module = importlib.import_module("faster_whisper")
        except Exception as exc:
            raise RuntimeError("Faster-Whisper backend unavailable: optional dependency is not installed") from exc
        return cast(Callable[..., Any], module.WhisperModel)

    def _load_model(self):
        if self._loaded_model is not None:
            return self._loaded_model
        with self._load_lock:
            if self._loaded_model is None:
                self._loaded_model = self._factory()(
                    str(self._resolved_model_path()),
                    device=self._device,
                    compute_type=self._compute_type,
                    local_files_only=True,
                )
        return self._loaded_model

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            cancellation_token=None,
        )

    def transcribe_with_control(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, object],
        cancellation_token: BackendCancellationToken,
        deadline_monotonic: float,
    ) -> TranscriptionResult:
        del context, deadline_monotonic
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            cancellation_token=cancellation_token,
        )

    def _transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        cancellation_token: BackendCancellationToken | None,
    ) -> TranscriptionResult:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        audio = self._decoder.decode(filename=filename, payload=content)
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        with temporary_audio_workspace() as workspace:
            input_path = workspace.write_bytes(
                "input.wav",
                audio.to_wav_bytes(),
                max_bytes=self._decode_limits.max_decoded_pcm_bytes + 4096,
            )
            raw_segments, info = self._load_model().transcribe(
                str(input_path),
                language=language,
                beam_size=self._beam_size,
                word_timestamps=True,
                vad_filter=self._vad_filter,
                vad_parameters={"min_silence_duration_ms": self._vad_min_silence_ms} if self._vad_filter else None,
            )
            segments_list: list[TranscriptionSegment] = []
            for segment in raw_segments:
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                segments_list.append(
                    self._map_segment(segment, duration_ms=audio.duration_ms)
                )
            segments = tuple(segments_list)

        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()

        text = " ".join(segment.text for segment in segments if segment.text).strip()
        probabilities = [segment.confidence for segment in segments if segment.confidence is not None]
        detected_language = getattr(info, "language", None) if info is not None else None
        return TranscriptionResult(
            text=text,
            language=str(detected_language or language or "und"),
            duration_ms=audio.duration_ms,
            model=self._model,
            segments=segments,
            confidence=sum(probabilities) / len(probabilities) if probabilities else None,
            raw_backend="faster_whisper",
        )

    @staticmethod
    def _map_segment(segment: Any, *, duration_ms: int) -> TranscriptionSegment:
        start_ms = max(0, min(duration_ms, int(float(getattr(segment, "start", 0) or 0) * 1000)))
        end_ms = max(start_ms, min(duration_ms, int(float(getattr(segment, "end", 0) or 0) * 1000)))
        probabilities = []
        words: list[TranscriptionWord] = []
        for word in getattr(segment, "words", None) or ():
            probability = getattr(word, "probability", None)
            if probability is not None:
                probabilities.append(max(0.0, min(1.0, float(probability))))
            word_start_ms = max(0, min(duration_ms, int(float(getattr(word, "start", 0) or 0) * 1000)))
            word_end_ms = max(
                word_start_ms,
                min(duration_ms, int(float(getattr(word, "end", 0) or 0) * 1000)),
            )
            word_text = str(getattr(word, "word", "") or "").strip()
            if word_text:
                words.append(
                    TranscriptionWord(
                        start_ms=word_start_ms,
                        end_ms=word_end_ms,
                        text=word_text,
                        confidence=max(0.0, min(1.0, float(probability))) if probability is not None else None,
                    )
                )
        confidence = sum(probabilities) / len(probabilities) if probabilities else None
        return TranscriptionSegment(
            start_ms=start_ms,
            end_ms=end_ms,
            text=str(getattr(segment, "text", "") or "").strip(),
            confidence=confidence,
            backend="faster_whisper",
            words=tuple(words),
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=None)

    def list_models(self) -> list[dict]:
        model_ready = False
        if self._model_path:
            try:
                model_ready = Path(self._model_path).expanduser().resolve(strict=True).is_dir()
            except OSError:
                model_ready = False
        dependency_ready = self._model_factory is not None or importlib.util.find_spec("faster_whisper") is not None
        return [
            {
                "id": self._model,
                "display_name": "Faster-Whisper local backend",
                "status": "available" if model_ready and dependency_ready else "unavailable",
                "model_path_configured": bool(self._model_path),
                "download_allowed": self._allow_download,
                "device": self._device,
                "compute_type": self._compute_type,
                "beam_size": self._beam_size,
                "vad_filter": self._vad_filter,
                "vad_min_silence_ms": self._vad_min_silence_ms,
                "capabilities": ["audio_input", "transcription", "offline", "local", "segments", "word_timestamps"],
            }
        ]

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()
