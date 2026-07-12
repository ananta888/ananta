from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from ..device import detect_runtime_device
from ..execution_control import BackendCancellationToken
from ..preprocessing.audio_decode import (
    AudioDecodeLimits,
    AudioDecoder,
    BoundedSubprocessRunner,
    ProcessOutputLimitError,
    ProcessPipeError,
    ProcessRunner,
    SafeAudioDecoder,
)
from ..preprocessing.temp_workspace import temporary_audio_workspace
from .base import ChatResult, TranscriptionResult, TranscriptionSegment, VoiceBackend


class VoxtralBackend(VoiceBackend):
    """Real local Voxtral subprocess adapter.

    Simulation is deliberately not part of this class. Missing runner/model files
    yield `unavailable`, allowing only policy-approved router fallback.
    """

    _MAX_OUTPUT_BYTES = 8 * 1024 * 1024
    _RUNNER_STYLES = {"realtime", "positional", "llama"}

    def __init__(
        self,
        *,
        model: str,
        fallback_model: str,
        preferred_device: str = "auto",
        model_path: str | None = None,
        runner_path: str | None = None,
        runner_style: str = "realtime",
        timeout_sec: int = 120,
        decoder: AudioDecoder | None = None,
        process_runner: ProcessRunner | None = None,
    ) -> None:
        self._model = model
        self._fallback_model = fallback_model
        self._device = detect_runtime_device(preferred_device)
        self._model_path = model_path
        self._runner_path = runner_path
        normalized_style = str(runner_style or "realtime").strip().lower()
        if normalized_style not in self._RUNNER_STYLES:
            raise ValueError("unsupported Voxtral runner style")
        self._runner_style = normalized_style
        self._timeout_sec = max(1, int(timeout_sec))
        self._decode_limits = AudioDecodeLimits()
        self._decoder = decoder or SafeAudioDecoder(limits=self._decode_limits)
        self._process_runner = process_runner or BoundedSubprocessRunner()
        self._readiness_lock = threading.Lock()
        self._runtime_probe_succeeded = False

    def name(self) -> str:
        return "voxtral"

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
        runner, model = self._validate_runtime_paths()
        audio = self._decoder.decode(filename=filename, payload=content)
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        with temporary_audio_workspace(prefix="ananta-voxtral-") as workspace:
            input_path = workspace.write_bytes(
                "input.wav",
                audio.to_wav_bytes(),
                max_bytes=self._decode_limits.max_decoded_pcm_bytes + 4_096,
            )
            argv = self.build_argv(runner=runner, model=model, audio=str(input_path))
            try:
                completed = self._process_runner.run(
                    argv,
                    input_payload=b"",
                    max_stdout_bytes=self._MAX_OUTPUT_BYTES,
                    timeout_seconds=(
                        max(
                            0.001,
                            cancellation_token.remaining_seconds(
                                maximum=float(self._timeout_sec)
                            ),
                        )
                        if cancellation_token is not None
                        else self._timeout_sec
                    ),
                    cwd=workspace.root,
                    cancellation_check=(
                        cancellation_token.raise_if_cancelled
                        if cancellation_token is not None
                        else None
                    ),
                )
            except ProcessOutputLimitError as exc:
                raise RuntimeError("Voxtral backend output exceeds the configured limit") from exc
            except ProcessPipeError as exc:
                raise RuntimeError("Voxtral backend output pipe failed") from exc
            except TimeoutError as exc:
                raise TimeoutError("Voxtral backend timed out") from exc
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            if completed.returncode != 0:
                raise RuntimeError(f"Voxtral backend failed with exit code {completed.returncode}")
            if len(completed.stdout) > self._MAX_OUTPUT_BYTES:
                raise RuntimeError("Voxtral backend output exceeds the configured limit")
            raw = completed.stdout.decode("utf-8", errors="replace")
        result = self.parse_output(raw, language=language, duration_ms=audio.duration_ms)
        # A path check alone never proves that the runner can load the pinned
        # weights.  A successful real runner invocation is the readiness probe;
        # health checks remain lightweight and never load the model themselves.
        with self._readiness_lock:
            self._runtime_probe_succeeded = True
        return result

    def build_argv(self, *, runner: str, model: str, audio: str) -> list[str]:
        if self._runner_style == "positional":
            return [runner, model, audio]
        if self._runner_style == "llama":
            return [runner, "--backend", "voxtral4b", "-m", model, "-f", audio]
        return [
            runner,
            "--model",
            model,
            "--audio",
            audio,
            "--threads",
            "4",
            "--max-len",
            "256",
            "--log-level",
            "warn",
        ]

    def parse_output(self, raw: str, *, language: str | None, duration_ms: int) -> TranscriptionResult:
        stripped = raw.strip()
        payload: dict[str, Any] = {}
        try:
            decoded = json.loads(stripped or "{}")
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            payload = {}
        text = str(payload.get("transcript") or payload.get("text") or "").strip()
        if not text:
            lines = [line.strip() for line in stripped.splitlines() if line.strip() and not line.startswith("[")]
            text = lines[-1] if lines else ""
        if not text:
            raise RuntimeError("Voxtral backend returned an empty transcript")
        confidence = _confidence(payload.get("confidence"))
        segment = TranscriptionSegment(
            start_ms=0,
            end_ms=duration_ms,
            text=text,
            confidence=confidence,
            backend="voxtral",
        )
        return TranscriptionResult(
            text=text,
            language=str(payload.get("language") or language or "und"),
            duration_ms=duration_ms,
            model=self._model,
            segments=(segment,),
            confidence=confidence,
            raw_backend="voxtral",
            provenance={
                "engine": "voxtral",
                "device": self._device.get("effective"),
                "execution_location": "voice-runtime",
                "synthetic": False,
            },
        )

    def _validate_runtime_paths(self) -> tuple[str, str]:
        runner_candidate = str(self._runner_path or "").strip()
        if not runner_candidate:
            raise RuntimeError("Voxtral backend unavailable: local runner is not configured")
        resolved_runner = (
            shutil.which(runner_candidate) if not Path(runner_candidate).is_absolute() else runner_candidate
        )
        try:
            runner = Path(str(resolved_runner or "")).expanduser().resolve(strict=True)
            model = Path(str(self._model_path or "")).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("Voxtral backend unavailable: local runtime files were not found") from exc
        if not runner.is_file() or not os.access(runner, os.X_OK):
            raise RuntimeError("Voxtral backend unavailable: local runner is not executable")
        if not model.is_file() or model.suffix.lower() != ".gguf" or not os.access(model, os.R_OK):
            raise RuntimeError("Voxtral backend unavailable: local GGUF model is invalid")
        return str(runner), str(model)

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content, language=None)
        intent = self._infer_intent(result.text, context=context)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=intent)

    def list_models(self) -> list[dict]:
        try:
            self._validate_runtime_paths()
            with self._readiness_lock:
                probed = self._runtime_probe_succeeded
            status = "ready" if probed else "degraded"
            reason_code = None if probed else "voxtral.runtime_probe_pending"
        except RuntimeError:
            status = "unavailable"
            reason_code = "voxtral.runtime_unavailable"
        return [
            {
                "id": self._model,
                "display_name": "Voxtral local backend",
                "engine": "voxtral",
                "status": status,
                "reason_code": reason_code,
                "synthetic": False,
                "capabilities": ["audio_input", "transcription", "voice_command", "offline", "local"],
                "device_preference": self._device.get("effective"),
            }
        ]

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()

    @staticmethod
    def _infer_intent(text: str, context: dict[str, Any] | None = None) -> dict | None:
        if not text:
            return None
        return {
            "type": "voice_command",
            "confidence": 0.82,
            "source": "voxtral",
            "context_keys": sorted(list((context or {}).keys())),
        }


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None
