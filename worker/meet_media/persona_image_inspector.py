"""Supervised execution port; image bytes never become an execution authority."""

import base64
import hashlib
import json
import math
import sys
import time
from pathlib import Path

from ananta_contracts.persona_image import MAX_INPUT_BYTES, decode_image
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner


class PersonaImageInspector:
    def __init__(self, *, require_current, deadline_monotonic, runner=None):
        now = time.monotonic()
        if (
            type(deadline_monotonic) not in (int, float)
            or not math.isfinite(deadline_monotonic)
            or not now < deadline_monotonic <= now + 30
        ):
            raise ValueError("persona_image_deadline_invalid")
        self.require_current = require_current
        self.deadline = deadline_monotonic
        self.runner = runner or BoundedSubprocessRunner()

    def inspect(self, content, media_type):
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= MAX_INPUT_BYTES
            or media_type not in ("image/png", "image/jpeg")
        ):
            raise ValueError("persona_image_input_invalid")
        self.require_current()
        remaining = min(5, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ValueError("persona_image_deadline_exceeded")
        try:
            response = self.runner.run(
                [sys.executable, "-m", "worker.meet_media.persona_image_child"],
                input_payload=json.dumps(
                    {"content": base64.b64encode(content).decode(), "media_type": media_type}
                ).encode(),
                max_stdout_bytes=8 * 1024 * 1024,
                timeout_seconds=remaining,
                cwd=Path(__file__).resolve().parents[2],
                cancellation_check=self.require_current,
            )
            if response.returncode != 0:
                raise ValueError("invalid")
            result = decode_image(json.loads(response.stdout), hashlib.sha256(content).hexdigest())
            self.require_current()
            if time.monotonic() >= self.deadline:
                raise ValueError("expired")
            return result
        except Exception:
            raise ValueError("persona_image_inspection_failed_or_revoked") from None
