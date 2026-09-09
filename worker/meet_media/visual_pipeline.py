"""Cancellable execution-only image analysis port, with no Hub or media storage."""

import json
import math
import sys
import threading
import time
from pathlib import Path

from ananta_contracts.meet_visual_receive import PROFILE, validate_visual_result
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner


class MeetVisualPipeline:
    def __init__(self, binding, lease, *, deadline_monotonic, runner=None):
        now = time.monotonic()
        if (
            type(deadline_monotonic) not in (float, int)
            or not math.isfinite(deadline_monotonic)
            or not now < deadline_monotonic <= now + 30
        ):
            raise ValueError("meet_visual_deadline_invalid")
        self.binding, self.lease, self.deadline = binding, lease, deadline_monotonic
        self.runner = runner or BoundedSubprocessRunner()
        self._cancelled = threading.Event()

    def analyze(self, frames):
        self._require()
        payload = json.dumps({"profile": PROFILE, "frames": frames}).encode()
        if len(payload) > 400_000:
            raise ValueError("meet_visual_input_too_large")
        try:
            response = self.runner.run(
                [sys.executable, "-m", "worker.meet_media.visual_child"],
                input_payload=payload,
                max_stdout_bytes=4096,
                timeout_seconds=min(5, self.deadline - time.monotonic()),
                cwd=Path(__file__).resolve().parents[2],
                cancellation_check=self._require,
            )
            if response.returncode != 0:
                raise ValueError("meet_visual_execution_failed")
            result = validate_visual_result(json.loads(response.stdout))
            self._require()
            return result
        except Exception:
            raise ValueError("meet_visual_failed_or_revoked") from None

    def _require(self):
        if self._cancelled.is_set() or time.monotonic() >= self.deadline:
            raise ValueError("meet_visual_cancelled")
        self.lease.require(self.binding)

    def cancel(self):
        self._cancelled.set()
