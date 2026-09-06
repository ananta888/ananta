"""Bounded pipe-only codec processes with one immutable caller deadline."""

import json
import math
import time
from pathlib import Path

from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from worker.meet_media.persona_video_probe import inspect_video_probe

PIPE_INPUT = [
    "-max_alloc",
    "67108864",
    "-protocol_whitelist",
    "pipe",
    "-f",
    "mov",
    "-threads",
    "1",
    "-err_detect",
    "explode",
    "-i",
    "pipe:0",
]
FFMPEG = ["/usr/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror"]


class PersonaVideoProcesses:
    def __init__(self, *, require_current, deadline_monotonic, runner=None):
        now = time.monotonic()
        if (
            type(deadline_monotonic) not in (int, float)
            or not math.isfinite(deadline_monotonic)
            or not now < deadline_monotonic <= now + 30
        ):
            raise ValueError("persona_video_deadline_invalid")
        self.require_current, self.deadline = require_current, deadline_monotonic
        self.runner = runner if runner is not None else BoundedSubprocessRunner()

    def run(self, command, content, maximum, seconds):
        self.require_current()
        budget = min(seconds, self.deadline - time.monotonic())
        if budget <= 0:
            raise ValueError("persona_video_deadline_exceeded")
        result = self.runner.run(
            command,
            input_payload=content,
            max_stdout_bytes=maximum,
            timeout_seconds=budget,
            cwd=Path(__file__).resolve().parent,
            cancellation_check=self.require_current,
        )
        self.require_current()
        if time.monotonic() >= self.deadline or result.returncode != 0:
            raise ValueError("persona_video_decoder_failed")
        return result.stdout

    def probe(self, content, *, normalized=False):
        raw = self.run(
            [
                "/usr/bin/ffprobe",
                "-v",
                "error",
                *PIPE_INPUT,
                "-count_frames",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,r_frame_rate,nb_read_frames,channels,sample_rate:format=format_name,duration",
                "-of",
                "json",
            ],
            content,
            131072,
            3,
        )
        return inspect_video_probe(json.loads(raw), normalized=normalized)
