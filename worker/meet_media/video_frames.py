"""One bounded fixed-format frame sequence; encoding is independent of its source."""

import math
import subprocess


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
    subprocess.run(
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
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            "350k",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(video_path),
        ],
        check=True,
        timeout=30,
        capture_output=True,
    )
    require_current()
    return video_path
