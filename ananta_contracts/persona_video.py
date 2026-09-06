"""Closed normalized clip result; bytes and hashes never confer usage rights."""

import base64
import hashlib
import re
from dataclasses import dataclass, field

from ananta_contracts.persona_image import png_dimensions

MAX_INPUT_BYTES = 3_500_000
MAX_VIDEO_BYTES = 1_500_000
MAX_PREVIEW_BYTES = 350_000
PROFILE = "ananta.persona-clip.h264-256-12.v1"


@dataclass(frozen=True)
class SanitizedPersonaVideo:
    source_sha256: str
    video_sha256: str
    preview_sha256: str
    frames: int
    video: bytes = field(repr=False)
    preview: bytes = field(repr=False)

    @property
    def duration_ms(self):
        return (self.frames * 1000 + 6) // 12


def encode_video(value):
    return {
        "schema": "ananta.persona-video-inspection.v1",
        "profile": PROFILE,
        "source_sha256": value.source_sha256,
        "video_sha256": value.video_sha256,
        "preview_sha256": value.preview_sha256,
        "frames": value.frames,
        "video": base64.b64encode(value.video).decode(),
        "preview": base64.b64encode(value.preview).decode(),
    }


def decode_video(value, source_sha256):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "profile",
            "source_sha256",
            "video_sha256",
            "preview_sha256",
            "frames",
            "video",
            "preview",
        }
        or value["schema"] != "ananta.persona-video-inspection.v1"
        or value["profile"] != PROFILE
    ):
        raise ValueError("persona_video_result_invalid")
    if (
        not isinstance(source_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", source_sha256)
        or value["source_sha256"] != source_sha256
    ):
        raise ValueError("persona_video_source_mismatch")
    if type(value["frames"]) is not int or not 2 <= value["frames"] <= 120:
        raise ValueError("persona_video_frame_budget_invalid")
    payloads = {}
    for name, maximum in (("video", MAX_VIDEO_BYTES), ("preview", MAX_PREVIEW_BYTES)):
        encoded = value[name]
        if not isinstance(encoded, str) or not 1 <= len(encoded) <= 4 * ((maximum + 2) // 3):
            raise ValueError("persona_video_result_size_invalid")
        content = base64.b64decode(encoded, validate=True)
        if not 16 <= len(content) <= maximum or hashlib.sha256(content).hexdigest() != value[name + "_sha256"]:
            raise ValueError("persona_video_result_digest_invalid")
        payloads[name] = content
    if payloads["video"][4:8] != b"ftyp" or png_dimensions(payloads["preview"]) != (256, 256):
        raise ValueError("persona_video_result_format_invalid")
    return SanitizedPersonaVideo(
        source_sha256,
        value["video_sha256"],
        value["preview_sha256"],
        value["frames"],
        **payloads,
    )
