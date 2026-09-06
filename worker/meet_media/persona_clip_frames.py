"""One bounded decoded clip, labelled and sampled under explicit repeat policy."""

import math

from PIL import Image, ImageDraw

from ananta_contracts.persona_video import decode_video, encode_video
from worker.meet_media.persona_video_processes import FFMPEG, PIPE_INPUT, PersonaVideoProcesses

FRAME_BYTES = 256 * 256 * 3


class PersonaClipFrames:
    def __init__(
        self, value, *, origin_kind, classification, repeat_mode, require_current, deadline_monotonic, runner=None
    ):
        # The caller must supply a Hub-authorized asset. Labels and hashes are
        # descriptive checks, not substitutes for that caller's lease/policy.
        if type(origin_kind) is not str or origin_kind not in {"upload", "generated", "licensed_pack"}:
            raise ValueError("persona_clip_origin_invalid")
        if type(classification) is not str or classification not in {"production", "synthetic", "test_only"}:
            raise ValueError("persona_clip_classification_invalid")
        if origin_kind == "generated" and classification == "production":
            raise ValueError("persona_clip_generated_classification_invalid")
        if type(repeat_mode) is not str or repeat_mode not in {"loop", "hold_last"}:
            raise ValueError("persona_clip_repeat_mode_required")
        processes = PersonaVideoProcesses(
            require_current=require_current, deadline_monotonic=deadline_monotonic, runner=runner
        )
        require_current()
        value = decode_video(encode_video(value), value.source_sha256)
        try:
            if processes.probe(value.video, normalized=True) != value.frames:
                raise ValueError("persona_clip_frame_count_mismatch")
            raw = processes.run(
                [
                    *FFMPEG,
                    *PIPE_INPUT,
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-threads",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                value.video,
                value.frames * FRAME_BYTES,
                5,
            )
            if len(raw) != value.frames * FRAME_BYTES:
                raise ValueError("persona_clip_frame_count_mismatch")
            require_current()
        except Exception:
            raise ValueError("persona_clip_decode_failed_or_revoked") from None
        self._raw, self.count = raw, value.frames
        self.repeat_mode, self.require_current = repeat_mode, require_current
        self.label = "ANANTA | AI | " + ("GENERATED" if origin_kind == "generated" else "IMPORTED")
        if classification != "production":
            self.label += " | " + ("TEST" if classification == "test_only" else "SYNTH")

    def frame(self, index):
        self.require_current()
        if self._raw is None:
            raise ValueError("persona_clip_closed")
        if type(index) is not int or not 0 <= index < 480:
            raise ValueError("persona_clip_frame_index_invalid")
        offset = (index % self.count if self.repeat_mode == "loop" else min(index, self.count - 1)) * FRAME_BYTES
        image = Image.frombytes("RGB", (256, 256), self._raw[offset : offset + FRAME_BYTES])
        # Always paint an opaque label over imported pixels; clip contents may
        # not hide the agent's identity. No talking-head/lip-sync claim.
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 222, 256, 256), fill="#101c30")
        parts = self.label.split(" | ")
        draw.text((8, 225), " | ".join(parts[:3]), fill="white")
        if len(parts) > 3:
            draw.text((8, 239), parts[3], fill="white")
        self.require_current()
        return image

    def close(self):
        self._raw = None  # Release retained pixels; not a secure memory-wipe claim.


def render_persona_clip(
    value,
    audio,
    duration,
    directory,
    *,
    origin_kind,
    classification,
    repeat_mode,
    require_current,
    deadline_monotonic,
    runner=None,
):
    from worker.meet_media.video_frames import encode_frames

    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 40:
        raise ValueError("meet_video_duration_invalid")
    frames = PersonaClipFrames(
        value,
        origin_kind=origin_kind,
        classification=classification,
        repeat_mode=repeat_mode,
        require_current=require_current,
        deadline_monotonic=deadline_monotonic,
        runner=runner,
    )
    try:
        return encode_frames(audio, duration, directory, frame_source=frames.frame, require_current=require_current)
    finally:
        frames.close()
