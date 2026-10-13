"""Closed synthetic clip protocol; preparation has no browser/policy authority."""

import base64
import binascii
import hashlib

MAX_PCM_BYTES = 1_940_400
MAX_REQUEST_BYTES = 2_600_000
MAX_RESPONSE_BYTES = 10_100_000
MAX_CLIPS = 5
MAX_MP4_BASE64_CHARS = 2_000_000
CLIP_FIELDS = frozenset({"mp4", "sha256", "frames", "repeatMode", "originKind", "classification"})
IDLE_STATES = frozenset({"idle", "thinking", "listening"})


def pcm_bytes(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("meet_companion_media_pcm_invalid")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("meet_companion_media_pcm_invalid") from None
    if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
        raise ValueError("meet_companion_media_pcm_invalid")
    return pcm


def validate_clip(clip, *, repeat):
    if (not isinstance(clip, dict) or set(clip) != CLIP_FIELDS
            or not isinstance(clip["mp4"], str) or not 0 < len(clip["mp4"]) <= MAX_MP4_BASE64_CHARS
            or type(clip["frames"]) is not int or not 1 <= clip["frames"] <= 120
            or clip["repeatMode"] != repeat or clip["originKind"] != "generated"
            or clip["classification"] != "synthetic"):
        raise ValueError("meet_companion_media_clip_invalid")
    try:
        data = base64.b64decode(clip["mp4"], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("meet_companion_media_clip_invalid") from None
    if len(data) < 12 or data[4:8] != b"ftyp" or hashlib.sha256(data).hexdigest() != clip["sha256"]:
        raise ValueError("meet_companion_media_clip_invalid")
    # Hash/shape are technical integrity checks, never registry evidence or a
    # decoder/sandbox proof. The existing browser still admits the MP4 format.
    return dict(clip)


def validate_clips(clips):
    if not isinstance(clips, list) or not 0 < len(clips) <= MAX_CLIPS:
        raise ValueError("meet_companion_media_clips_invalid")
    return [validate_clip(clip, repeat="hold_last") for clip in clips]
