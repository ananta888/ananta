"""Supervised execution port; image bytes never become an execution authority."""

import base64
import hashlib
import json
import math
import struct
import sys
import time
from pathlib import Path

from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from worker.meet_media.persona_image import MAX_INPUT_BYTES, SanitizedPersonaImage


def _png_dimensions(content):
    if len(content) < 33 or content[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or content[24:26] != b"\x08\x06":
        raise ValueError("persona_image_header_invalid")
    return struct.unpack(">II", content[16:24])


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
            result = json.loads(response.stdout)
            if not isinstance(result, dict) or set(result) != {
                "schema",
                "source_sha256",
                "image_sha256",
                "preview_sha256",
                "width",
                "height",
                "png",
                "preview",
            }:
                raise ValueError("invalid")
            if (
                result["schema"] != "ananta.persona-image-inspection.v1"
                or result["source_sha256"] != hashlib.sha256(content).hexdigest()
            ):
                raise ValueError("invalid")
            png = base64.b64decode(result["png"], validate=True)
            preview = base64.b64decode(result["preview"], validate=True)
            if (
                not 0 < len(png) <= MAX_INPUT_BYTES
                or not 0 < len(preview) <= 350_000
                or hashlib.sha256(png).hexdigest() != result["image_sha256"]
                or hashlib.sha256(preview).hexdigest() != result["preview_sha256"]
                or any(type(result[key]) is not int or not 0 < result[key] <= 1024 for key in ("width", "height"))
            ):
                raise ValueError("invalid")
            if _png_dimensions(png) != (result["width"], result["height"]) or any(
                not 0 < dimension <= 256 for dimension in _png_dimensions(preview)
            ):
                raise ValueError("invalid")
            self.require_current()
            if time.monotonic() >= self.deadline:
                raise ValueError("expired")
            return SanitizedPersonaImage(
                source_sha256=result["source_sha256"],
                image_sha256=result["image_sha256"],
                preview_sha256=result["preview_sha256"],
                width=result["width"],
                height=result["height"],
                png=png,
                preview=preview,
            )
        except Exception:
            raise ValueError("persona_image_inspection_failed_or_revoked") from None
