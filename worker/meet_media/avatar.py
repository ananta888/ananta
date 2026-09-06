"""Simple amplitude-driven avatar rendering and NVIDIA video encoding."""

import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw


def avatar(audio, samples, rate, duration, directory):
    """Procedural, visibly synthetic face; amplitude animation, not lip synthesis."""
    fps, size = 12, 256
    raw_path, video_path = directory / "frames.rgb", directory / "avatar.mp4"
    with raw_path.open("wb") as raw:
        for index in range(math.ceil(duration * fps)):
            frame = Image.new("RGB", (size, size), "#101c30")
            draw = ImageDraw.Draw(frame)
            draw.rounded_rectangle((45, 28, 211, 206), radius=55, fill="#4365cc")
            draw.ellipse((78, 80, 94, 100), fill="white")
            draw.ellipse((162, 80, 178, 100), fill="white")
            chunk = samples[int(index * rate / fps) : int((index + 1) * rate / fps)]
            amplitude = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0
            opening = 3 + min(28, int(amplitude * 140))
            draw.ellipse((100, 143 - opening, 156, 143 + opening), fill="#101c30")
            draw.text((85, 222), "ANANTA | AI", fill="white")
            raw.write(frame.tobytes())
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
            str(fps),
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
    return video_path
