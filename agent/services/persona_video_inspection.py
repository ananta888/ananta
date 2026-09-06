"""Closed video inspection result and metadata receipt; never an identity issuer."""

import hashlib
import json
from dataclasses import dataclass, field

from ananta_contracts.persona_video import PROFILE, SanitizedPersonaVideo, decode_video, encode_video


@dataclass(frozen=True)
class PersonaVideoInspectionResult:
    task_id: str
    lease_id: str
    video: SanitizedPersonaVideo = field(repr=False)
    run_id: str
    assignment_id: str
    run_binding_digest: str


def video_receipt_digest(*, source_sha256, video_sha256, preview_sha256, frames, video_size, preview_size):
    payload = {
        "schema": "ananta.persona-video-receipt.v1",
        "profile": PROFILE,
        "source_sha256": source_sha256,
        "video_sha256": video_sha256,
        "preview_sha256": preview_sha256,
        "frames": frames,
        "video_size": video_size,
        "preview_size": preview_size,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def video_receipt(video, expected_source):
    value = decode_video(encode_video(video), expected_source)
    return video_receipt_digest(
        source_sha256=value.source_sha256,
        video_sha256=value.video_sha256,
        preview_sha256=value.preview_sha256,
        frames=value.frames,
        video_size=len(value.video),
        preview_size=len(value.preview),
    )
