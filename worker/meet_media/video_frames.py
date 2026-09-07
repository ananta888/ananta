"""One bounded fixed-format frame sequence; encoding is independent of its source."""

import math
import subprocess

from ananta_contracts.meet_media_failures import MediaCapabilityError
from worker.meet_media.av_quality import MAX_VIDEO_BYTES, verify_encoded_media
from worker.meet_media.video_encoder_profile import NVENC_OPTIONS


def encode_frames(audio, duration, directory, *, frame_source, require_current):
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 40:
        raise ValueError("meet_video_duration_invalid")
    raw_path, video_path = directory / "frames.rgb", directory / "avatar.mp4"
    with raw_path.open("wb") as raw:
        for index in range(math.ceil(duration * 12)):
            require_current()
            frame = frame_source(index)
            if frame.mode != "RGB" or frame.size != (256, 256):
                raise ValueError("meet_video_frame_format_invalid")
            raw.write(frame.tobytes())
    require_current()
    _encode(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            "256x256",
            "-framerate",
            "12",
            "-i",
            str(raw_path),
            "-i",
            str(audio),
            *NVENC_OPTIONS,
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(video_path),
        ],
    )
    require_current()
    with video_path.open("rb") as output:
        verify_encoded_media(
            output.read(MAX_VIDEO_BYTES + 1),
            expected_samples=round(duration * 22050),
            require_current=require_current,
        )
    return video_path


def _encode(command):
    """Keep private FFmpeg diagnostics local; never retry via another codec."""
    try:
        subprocess.run(command, check=True, timeout=30, capture_output=True)
    except subprocess.TimeoutExpired:
        raise MediaCapabilityError("meet_video_encoder_timeout") from None
    except (OSError, subprocess.CalledProcessError):
        raise MediaCapabilityError("meet_video_encoder_unavailable_or_failed") from None
