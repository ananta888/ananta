"""Supervised local rendering and one fixed CPU codec; no fallback or tasks."""

import math
import sys
import time
from pathlib import Path

from ananta_contracts.persona_generation import MAX_OUTPUT, RAW_VIDEO_BYTES, recipe_bytes, validate_recipe
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner

VIDEO_COMMAND = (
    "/usr/bin/ffmpeg",
    "-nostdin",
    "-hide_banner",
    "-loglevel",
    "error",
    "-xerror",
    "-max_alloc",
    "67108864",
    "-protocol_whitelist",
    "pipe",
    "-f",
    "rawvideo",
    "-pixel_format",
    "rgb24",
    "-video_size",
    "256x256",
    "-framerate",
    "12",
    "-i",
    "pipe:0",
    "-an",
    "-sn",
    "-dn",
    "-map_metadata",
    "-1",
    "-frames:v",
    "24",
    "-c:v",
    "libx264",
    "-threads",
    "1",
    "-preset",
    "ultrafast",
    "-pix_fmt",
    "yuv420p",
    "-b:v",
    "200k",
    "-maxrate",
    "200k",
    "-bufsize",
    "400k",
    "-movflags",
    "frag_keyframe+empty_moov+default_base_moof",
    "-f",
    "mp4",
    "pipe:1",
)


class ProceduralPersonaGenerator:
    def __init__(self, *, require_current, deadline_monotonic, runner=None):
        now = time.monotonic()
        if (
            type(deadline_monotonic) not in (float, int)
            or not math.isfinite(deadline_monotonic)
            or not now < deadline_monotonic <= now + 20
        ):
            raise ValueError("persona_generation_deadline_invalid")
        self.require_current, self.deadline = require_current, deadline_monotonic
        self.runner = runner if runner is not None else BoundedSubprocessRunner()

    def _run(self, command, content, maximum):
        self.require_current()
        seconds = min(5, self.deadline - time.monotonic())
        if seconds <= 0:
            raise ValueError("persona_generation_expired")
        result = self.runner.run(
            command,
            input_payload=content,
            max_stdout_bytes=maximum,
            timeout_seconds=seconds,
            cwd=Path(__file__).resolve().parents[2],
            cancellation_check=self.require_current,
        )
        self.require_current()
        if result.returncode != 0 or time.monotonic() >= self.deadline or len(result.stdout) > maximum:
            raise ValueError("persona_generation_process_failed")
        return result.stdout

    def generate(self, recipe):
        validate_recipe(recipe)
        try:
            image = recipe["media_kind"] == "image"
            rendered = self._run(
                [sys.executable, "-m", "worker.meet_media.persona_generation_frames"],
                recipe_bytes(recipe),
                MAX_OUTPUT if image else RAW_VIDEO_BYTES,
            )
            if image:
                return rendered
            if len(rendered) != RAW_VIDEO_BYTES:
                raise ValueError("persona_generation_frame_count_invalid")
            return self._run(list(VIDEO_COMMAND), rendered, MAX_OUTPUT)
        except Exception:
            raise ValueError("persona_generation_failed_or_revoked") from None
