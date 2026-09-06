"""Execution-only adapter around the existing local Voice backend in a child."""

import base64
import json
import sys
import sysconfig
import threading
import time
from pathlib import Path

from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from worker.meet_media.asr_model import REVISION


class MeetAsrPipeline:
    def __init__(self, binding, lease, *, deadline_monotonic, runner=None):
        self.binding, self.lease, self.deadline = binding, lease, deadline_monotonic
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
        remaining = min(20, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ValueError("meet_asr_deadline_exceeded")
        self._require()
        try:
            response = self.runner.run(
                [sys.executable, "-m", "worker.meet_media.asr_child"],
                input_payload=json.dumps({"wav": base64.b64encode(content).decode(), "language": language}).encode(),
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
