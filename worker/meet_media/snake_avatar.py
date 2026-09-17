"""Friendly snake avatar with an amplitude-driven (speech-synced) mouth.

Renders 256x256 RGB frames and encodes a muted, video-only H.264 MP4 at 12 fps
so the room client's persona-video-v1 decoder can loop or hold it. The mouth
opening follows the local speech envelope, so the lips move with the audio.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

FPS = 12
SIZE = 256
MAX_FRAMES = 120

_BODY = "#3fae56"
_BODY_LIGHT = "#5ecb74"
_HEAD = "#4cbb63"
_HEAD_LIGHT = "#63d07a"
_MOUTH = "#5a1330"
_TONGUE = "#c2506a"
_EYE = "#ffffff"
_PUPIL = "#10241a"
_OUTLINE = "#255c33"
_BG = "#e8f6ec"


def _amplitude(samples, index, rate):
    chunk = samples[int(index * rate / FPS) : int((index + 1) * rate / FPS)]
    if chunk.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(chunk ** 2)))


def snake_frame(index, samples, rate):
    """One friendly, smiling snake frame; the mouth opens with the envelope."""
    img = Image.new("RGB", (SIZE, SIZE), _BG)
    draw = ImageDraw.Draw(img)

    # Coiled body resting on the ground.
    draw.ellipse((24, 158, 232, 252), fill=_OUTLINE)
    draw.ellipse((30, 162, 226, 246), fill=_BODY)
    draw.ellipse((52, 178, 204, 232), fill=_BODY_LIGHT)
    draw.ellipse((86, 194, 170, 226), fill=_BODY)

    # Neck rising to the head.
    draw.polygon(((116, 150), (140, 150), (152, 96), (104, 96)), fill=_BODY)

    # Head with a rounded snout.
    draw.ellipse((70, 37, 186, 137), fill=_OUTLINE)
    draw.ellipse((75, 42, 181, 132), fill=_HEAD)
    draw.ellipse((88, 55, 168, 118), fill=_HEAD_LIGHT)

    # Friendly eyes with highlight.
    for cx in (108, 148):
        draw.ellipse((cx - 12, 60, cx + 12, 86), fill=_EYE)
        draw.ellipse((cx - 5, 66, cx + 5, 80), fill=_PUPIL)
        draw.ellipse((cx - 2, 68, cx + 2, 72), fill=_EYE)

    # Nostrils.
    draw.ellipse((120, 92, 124, 96), fill=_OUTLINE)
    draw.ellipse((132, 92, 136, 96), fill=_OUTLINE)

    # Smiling mouth that opens with the speech envelope.
    opening = int(min(24, _amplitude(samples, index, rate) * 210))
    draw.arc((98, 98, 158, 126), start=25, end=155, fill=_OUTLINE, width=3)
    if opening > 0:
        draw.ellipse((110, 100, 146, 100 + opening), fill=_MOUTH)
        tongue_bottom = 100 + max(6, opening - 4)
        if opening > 10 and tongue_bottom > 106:
            draw.ellipse((118, 106, 138, tongue_bottom), fill=_TONGUE)

    # Name tag.
    draw.text((92, 232), "ai-snake", fill=_OUTLINE)
    return img


def build_video(samples, rate, seconds, directory):
    """Encode a muted video-only 256x256 H.264 MP4; returns (bytes, frames)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    frames = max(2, min(MAX_FRAMES, int(round(max(0.2, float(seconds)) * FPS))))
    raw_path = directory / "snake.rgb"
    video_path = directory / "snake.mp4"
    with raw_path.open("wb") as raw:
        for index in range(frames):
            raw.write(snake_frame(index, samples, rate).tobytes())

    if os.environ.get("ANANTA_CPU_FALLBACK") == "1":
        codec = ("-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-b:v", "400k")
    else:
        codec = ("-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ull", "-pix_fmt", "yuv420p", "-bf", "0", "-zerolatency", "1", "-b:v", "400k")

    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{SIZE}x{SIZE}",
        "-framerate", str(FPS), "-i", str(raw_path),
        *codec, "-movflags", "+faststart", str(video_path),
    ]
    try:
        subprocess.run(command, check=True, timeout=60, capture_output=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError("meet_snake_video_encode_failed")
    data = video_path.read_bytes()
    if len(data) < 16 or data[4:8] != b"ftyp":
        raise ValueError("meet_snake_video_invalid")
    return data, frames


def video_payload(data, frames, *, repeat="hold_last"):
    import base64

    if repeat not in ("loop", "hold_last"):
        raise ValueError("meet_snake_repeat_invalid")
    return {
        "mp4": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "frames": int(frames),
        "repeatMode": repeat,
        "originKind": "generated",
        "classification": "synthetic",
    }