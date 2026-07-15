from __future__ import annotations

import importlib
import importlib.util
import json
import threading
from pathlib import Path
from typing import Any

from ..execution_control import BackendCancellationToken
from ..preprocessing.audio_decode import AudioDecoder, SafeAudioDecoder
from .base import ChatResult, TranscriptionResult, TranscriptionSegment, TranscriptionWord, VoiceBackend


class VoskBackend(VoiceBackend):
    """Local-only Vosk adapter with lazy, thread-safe model loading."""

    _CHUNK_BYTES = 8_000

    def __init__(
        self,
        *,
        model_path: str | None = None,
        model: str = "vosk",
        decoder: AudioDecoder | None = None,
        vosk_module: Any | None = None,
    ) -> None:
        self._model_path = model_path
        self._model = model
        self._decoder = decoder or SafeAudioDecoder()
        self._vosk_module = vosk_module
        self._loaded_model: Any | None = None
        self._load_lock = threading.Lock()

    def name(self) -> str:
        return "vosk"

    def _resolved_model_path(self) -> Path:
        if not self._model_path:
            raise RuntimeError("vosk backend unavailable: VOICE_VOSK_MODEL_PATH is not configured")
        try:
            path = Path(self._model_path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("vosk backend unavailable: configured model path does not exist") from exc
        if not path.is_dir():
            raise RuntimeError("vosk backend unavailable: configured model path is not a directory")
        return path

    def _module(self):
        if self._vosk_module is not None:
            return self._vosk_module
        try:
            self._vosk_module = importlib.import_module("vosk")
        except Exception as exc:
            raise RuntimeError("vosk backend unavailable: optional dependency 'vosk' is not installed") from exc
        return self._vosk_module

    def _load_model(self):
        if self._loaded_model is not None:
            return self._loaded_model
        with self._load_lock:
            if self._loaded_model is None:
                module = self._module()
                self._loaded_model = module.Model(str(self._resolved_model_path()))
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
        module = self._module()
        recognizer = module.KaldiRecognizer(self._load_model(), audio.sample_rate_hz)
        if hasattr(recognizer, "SetWords"):
            recognizer.SetWords(True)

        result_payloads: list[dict[str, Any]] = []
        for offset in range(0, len(audio.pcm_s16le), self._CHUNK_BYTES):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            chunk = audio.pcm_s16le[offset : offset + self._CHUNK_BYTES]
            if recognizer.AcceptWaveform(chunk):
                parsed = self._parse_result(recognizer.Result())
                if parsed:
                    result_payloads.append(parsed)
        final = self._parse_result(recognizer.FinalResult())
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        if final:
            result_payloads.append(final)
        return self._build_result(result_payloads, duration_ms=audio.duration_ms, language=language)

    def _build_result(
        self,
        payloads: list[dict[str, Any]],
        *,
        duration_ms: int,
        language: str | None,
    ) -> TranscriptionResult:
        text_parts: list[str] = []
        segments: list[TranscriptionSegment] = []
        confidences: list[float] = []
        for payload in payloads:
            text = str(payload.get("text") or "").strip()
            if text:
                text_parts.append(text)
            words = payload.get("result")
            if not isinstance(words, list):
                continue
            for word in words:
                if not isinstance(word, dict):
                    continue
                word_text = str(word.get("word") or "").strip()
                if not word_text:
                    continue
                start_ms = max(0, min(duration_ms, int(float(word.get("start") or 0) * 1000)))
                end_ms = max(
                    start_ms,
                    min(duration_ms, int(float(word.get("end") or word.get("start") or 0) * 1000)),
                )
                confidence = self._confidence(word.get("conf"))
                if confidence is not None:
                    confidences.append(confidence)
                segments.append(
                    TranscriptionSegment(
                        start_ms=start_ms,
                        end_ms=min(duration_ms, end_ms),
                        text=word_text,
                        confidence=confidence,
                        backend="vosk",
                        words=(
                            TranscriptionWord(
                                start_ms=start_ms,
                                end_ms=end_ms,
                                text=word_text,
                                confidence=confidence,
                            ),
                        ),
                    )
                )

        text = " ".join(text_parts).strip()
        confidence = sum(confidences) / len(confidences) if confidences else None
        if text and not segments:
            segments.append(
                TranscriptionSegment(
                    start_ms=0,
                    end_ms=duration_ms,
                    text=text,
                    confidence=confidence,
                    backend="vosk",
                )
            )
        return TranscriptionResult(
            text=text,
            language=language or "und",
            duration_ms=duration_ms,
            model=self._model,
            segments=tuple(segments),
            confidence=confidence,
            raw_backend="vosk",
        )

    @staticmethod
    def _parse_result(raw: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _confidence(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=None)

    def list_models(self) -> list[dict]:
        path_ready = False
        if self._model_path:
            try:
                path_ready = Path(self._model_path).expanduser().resolve(strict=True).is_dir()
            except OSError:
                path_ready = False
        dependency_ready = self._vosk_module is not None or importlib.util.find_spec("vosk") is not None
        available = path_ready and dependency_ready
        reason_code = None
        if not path_ready:
            reason_code = "vosk.model_unavailable"
        elif not dependency_ready:
            reason_code = "vosk.dependency_unavailable"
        return [
            {
                "id": self._model,
                "display_name": "Vosk local backend",
                "status": "available" if available else "unavailable",
                "reason_code": reason_code,
                "model_path_configured": bool(self._model_path),
                "capabilities": ["audio_input", "transcription", "offline", "local", "word_timestamps"],
            }
        ]

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()

    def create_incremental_recognizer(
        self,
        *,
        filename: str,
        language: str | None,
        max_bytes: int,
    ) -> "VoskIncrementalRecognizer":
        del filename
        module = self._module()
        recognizer = module.KaldiRecognizer(self._load_model(), 16_000)
        if hasattr(recognizer, "SetWords"):
            recognizer.SetWords(True)
        return VoskIncrementalRecognizer(
            backend=self,
            recognizer=recognizer,
            language=language,
            max_bytes=max_bytes,
        )


class VoskIncrementalRecognizer:
    def __init__(
        self,
        *,
        backend: VoskBackend,
        recognizer: Any,
        language: str | None,
        max_bytes: int,
    ) -> None:
        self._backend = backend
        self._recognizer = recognizer
        self._language = language
        self._max_bytes = max_bytes
        self._accepted_bytes = 0
        self._payloads: list[dict[str, Any]] = []
        self._closed = False

    def accept(self, content: bytes) -> str | None:
        if self._closed:
            raise RuntimeError("Vosk stream is closed")
        if self._accepted_bytes + len(content) > self._max_bytes:
            raise ValueError("Vosk stream exceeds its byte budget")
        if len(content) % 2:
            raise ValueError("Vosk PCM chunks must contain complete 16-bit samples")
        self._accepted_bytes += len(content)
        if self._recognizer.AcceptWaveform(content):
            parsed = self._backend._parse_result(self._recognizer.Result())
            if parsed:
                self._payloads.append(parsed)
                return str(parsed.get("text") or "").strip() or None
        partial = self._backend._parse_result(self._recognizer.PartialResult())
        return str((partial or {}).get("partial") or "").strip() or None

    def finish(self) -> TranscriptionResult:
        if self._closed:
            raise RuntimeError("Vosk stream is closed")
        final = self._backend._parse_result(self._recognizer.FinalResult())
        if final:
            self._payloads.append(final)
        duration_ms = self._accepted_bytes * 1000 // (16_000 * 2)
        return self._backend._build_result(
            self._payloads,
            duration_ms=duration_ms,
            language=self._language,
        )

    def close(self) -> None:
        self._payloads.clear()
        self._recognizer = None
        self._closed = True
