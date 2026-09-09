"""Execution-only adapter around the existing local Voice backend in a child."""

import base64
import json
import math
import sys
import sysconfig
import threading
import time
from pathlib import Path

from ananta_contracts.meet_audio_profile import parse_audio_profile
from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from worker.meet_media.asr_model import REVISION


class MeetAsrPipeline:
    def __init__(self, binding, lease, *, deadline_monotonic, runner=None, audio_profile=None):
        now = time.monotonic()
        if (
            type(deadline_monotonic) not in (int, float)
            or not math.isfinite(deadline_monotonic)
            or not now < deadline_monotonic <= now + 30
        ):
            raise ValueError("meet_asr_deadline_invalid")
        self.binding, self.lease, self.deadline = binding, lease, deadline_monotonic
        self.audio_profile = parse_audio_profile(audio_profile) if audio_profile is not None else None
        self._cancelled = threading.Event()
        self.runner = runner or BoundedSubprocessRunner(
            library_paths=(
                "/host-nvidia",
                str(Path(sysconfig.get_paths()["purelib"]) / "nvidia/cublas/lib"),
                str(Path(sysconfig.get_paths()["purelib"]) / "nvidia/cudnn/lib"),
                str(Path(sysconfig.get_paths()["purelib"]) / "nvidia/cuda_runtime/lib"),
            )
        )

    def transcribe(self, *, filename, content, language=None, context=None):
        # No caller path, personalization, transcript history or instructions
        # cross into this local ASR profile. Authority is the bound lease only.
        del filename, context
        if language not in ("de", "en") or not isinstance(content, bytes) or not 44 < len(content) <= 320_044:
            raise ValueError("meet_asr_input_invalid")
        payload = {"wav": base64.b64encode(content).decode(), "language": language}
        if self.audio_profile is not None:
            if language != self.audio_profile.language or len(content) > self.audio_profile.end_sample * 2 + 44:
                raise ValueError("meet_asr_profile_mismatch")
            payload["audio_profile"] = self.audio_profile.projection()
        remaining = min(20, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ValueError("meet_asr_deadline_exceeded")
        self._require()
        try:
            response = self.runner.run(
                [sys.executable, "-m", "worker.meet_media.asr_child"],
                input_payload=json.dumps(payload).encode(),
                max_stdout_bytes=12_000,
                timeout_seconds=remaining,
                cwd=Path(__file__).resolve().parents[2],
                cancellation_check=self._require,
            )
            if response.returncode != 0:
                raise ValueError("meet_asr_execution_failed")
            result = json.loads(response.stdout)
            if (
                not isinstance(result, dict)
                or set(result) != {"schema", "text", "language", "duration_ms", "model_revision", "device"}
                or result["schema"] != "ananta.meet-asr-result.v1"
                or result["model_revision"] != REVISION
                or result["device"] != "cuda"
                or result["language"] != language
                or not isinstance(result["text"], str)
                or len(result["text"]) > 2000
                or type(result["duration_ms"]) is not int
                or not 0 < result["duration_ms"] <= 10_000
                or self.audio_profile is not None
                and result["duration_ms"] > self.audio_profile.segment_seconds * 1000
            ):
                raise ValueError("meet_asr_result_invalid")
            self._require()
            if time.monotonic() >= self.deadline:
                raise ValueError("meet_asr_deadline_exceeded")
            return TranscriptionResult(
                text=result["text"],
                language=language,
                duration_ms=result["duration_ms"],
                model="whisper-small-pinned",
                raw_backend="faster_whisper",
            )
        except Exception:
            raise ValueError("meet_asr_failed_or_revoked") from None

    def cancel(self):
        self._cancelled.set()

    def _require(self):
        if self._cancelled.is_set():
            raise ValueError("meet_asr_cancelled")
        self.lease.require(self.binding)
