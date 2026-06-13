from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import ChatResult, TranscriptionResult, TranscriptionSegment, VoiceBackend


class WhisperCppBackend(VoiceBackend):
    """Optional whisper.cpp adapter with argv-only subprocess execution."""

    def __init__(
        self,
        *,
        binary: str | None,
        model_path: str | None,
        extra_args: tuple[str, ...] = (),
        timeout_sec: int = 120,
        model: str = "whisper_cpp",
    ) -> None:
        self._binary = binary
        self._model_path = model_path
        self._extra_args = tuple(extra_args)
        self._timeout_sec = max(1, timeout_sec)
        self._model = model

    def name(self) -> str:
        return "whisper_cpp"

    def build_argv(self, *, input_path: str, output_path: str, language: str | None = None) -> list[str]:
        if not self._binary:
            raise RuntimeError("whisper.cpp backend unavailable: VOICE_WHISPER_CPP_BIN is not configured")
        if not self._model_path:
            raise RuntimeError("whisper.cpp backend unavailable: VOICE_WHISPER_CPP_MODEL_PATH is not configured")
        argv = [
            self._binary,
            "-m",
            self._model_path,
            "-f",
            input_path,
            "-oj",
            "-of",
            str(Path(output_path).with_suffix("")),
        ]
        if language:
            argv.extend(["-l", language])
        argv.extend(self._extra_args)
        return argv

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        if not self._binary or not self._model_path:
            self.build_argv(input_path=filename or "audio", output_path="out.json", language=language)
        with tempfile.TemporaryDirectory(prefix="ananta-voice-") as tmpdir:
            input_path = Path(tmpdir) / (Path(filename or "audio.wav").name or "audio.wav")
            output_path = Path(tmpdir) / "transcript.json"
            input_path.write_bytes(content)
            argv = self.build_argv(input_path=str(input_path), output_path=str(output_path), language=language)
            try:
                completed = subprocess.run(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("whisper.cpp backend timeout") from exc
            if completed.returncode != 0:
                raise RuntimeError(f"whisper.cpp backend failed: {completed.stderr.strip() or completed.returncode}")
            payload_path = output_path if output_path.exists() else output_path.with_suffix(".json")
            raw = payload_path.read_text(encoding="utf-8") if payload_path.exists() else completed.stdout
        return self.parse_json_output(raw, language=language)

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=None)

    def list_models(self) -> list[dict]:
        return [
            {
                "id": self._model,
                "display_name": "whisper.cpp optional backend",
                "status": "available" if self._binary and self._model_path else "unavailable",
                "binary": self._binary,
                "model_path": self._model_path,
                "capabilities": ["audio_input", "transcription", "offline", "local", "segments"],
            }
        ]

    def parse_json_output(self, raw: str, *, language: str | None = None) -> TranscriptionResult:
        try:
            payload = json.loads(raw or "{}")
        except ValueError:
            text = " ".join((raw or "").split())
            return TranscriptionResult(
                text=text,
                language=language or "und",
                model=self._model,
                warnings=("whisper_cpp_unstructured_output",),
                confidence=None,
                raw_backend="whisper_cpp",
            )
        segments_payload = payload.get("transcription") or payload.get("segments") or []
        segments = tuple(self._parse_segment(item) for item in segments_payload if isinstance(item, dict))
        text = str(payload.get("text") or " ".join(segment.text for segment in segments)).strip()
        confidence_values = [segment.confidence for segment in segments if segment.confidence is not None]
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
        duration_ms = max((segment.end_ms for segment in segments), default=None)
        return TranscriptionResult(
            text=text,
            language=str(payload.get("language") or language or "und"),
            duration_ms=duration_ms,
            model=self._model,
            segments=segments,
            confidence=confidence,
            raw_backend="whisper_cpp",
        )

    @staticmethod
    def _parse_segment(item: dict[str, Any]) -> TranscriptionSegment:
        start = item.get("start_ms", item.get("from_ms", item.get("start", item.get("from", 0))))
        end = item.get("end_ms", item.get("to_ms", item.get("end", item.get("to", 0))))
        if isinstance(start, float):
            start = int(start * 1000)
        if isinstance(end, float):
            end = int(end * 1000)
        return TranscriptionSegment(
            start_ms=int(start or 0),
            end_ms=max(int(end or 0), int(start or 0)),
            text=str(item.get("text") or "").strip(),
            confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
            backend="whisper_cpp",
        )
