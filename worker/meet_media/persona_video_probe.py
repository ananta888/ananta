"""Fixed bounded clip profile checks, independent from process execution."""

import re
from decimal import Decimal
from fractions import Fraction


def inspect_video_probe(probe, *, normalized=False):
    if not isinstance(probe, dict) or not isinstance(probe.get("streams"), list):
        raise ValueError("persona_video_streams_invalid")
    streams = probe["streams"]
    if not 1 <= len(streams) <= (1 if normalized else 2) or any(not isinstance(item, dict) for item in streams):
        raise ValueError("persona_video_streams_invalid")
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    if len(video) != 1 or len(video) + len(audio) != len(streams) or len(audio) > 1:
        raise ValueError("persona_video_streams_invalid")
    video = video[0]
    if video.get("codec_name") != "h264":
        raise ValueError("persona_video_codec_unsupported")
    for name, maximum in (("width", 1280), ("height", 720)):
        value = video.get(name)
        if type(value) is not int or not 2 <= value <= maximum or (normalized and value != 256):
            raise ValueError("persona_video_dimensions_invalid")
    rate, frames = video.get("r_frame_rate"), video.get("nb_read_frames")
    if (
        not isinstance(rate, str)
        or not re.fullmatch(r"[0-9]{1,5}/[0-9]{1,5}", rate)
        or not isinstance(frames, str)
        or not re.fullmatch(r"[0-9]{1,3}", frames)
    ):
        raise ValueError("persona_video_clock_invalid")
    try:
        fps = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        raise ValueError("persona_video_clock_invalid") from None
    if not 1 <= fps <= 30 or not 2 <= int(frames) <= (120 if normalized else 300) or (normalized and fps != 12):
        raise ValueError("persona_video_frame_budget_invalid")
    for item in audio:
        if (
            item.get("codec_name") != "aac"
            or type(item.get("channels")) is not int
            or not 1 <= item["channels"] <= 2
            or not isinstance(item.get("sample_rate"), str)
            or item.get("sample_rate") not in {"16000", "22050", "44100", "48000"}
        ):
            raise ValueError("persona_video_audio_unsupported")
    container = probe.get("format")
    if not isinstance(container, dict) or container.get("format_name") != "mov,mp4,m4a,3gp,3g2,mj2":
        raise ValueError("persona_video_container_invalid")
    duration = container.get("duration")
    if not isinstance(duration, str) or not re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,6})?", duration):
        raise ValueError("persona_video_duration_invalid")
    duration = Decimal(duration)
    if not 0 < duration <= 10:
        raise ValueError("persona_video_duration_invalid")
    if normalized and abs(duration - Decimal(int(frames)) / 12) > Decimal("0.00001"):
        raise ValueError("persona_video_extent_invalid")
    return int(frames)
