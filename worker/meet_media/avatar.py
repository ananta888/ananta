"""Simple amplitude-driven avatar rendering and NVIDIA video encoding."""

import numpy as np
from PIL import Image, ImageDraw

from worker.meet_media.video_frames import encode_frames


def _frame(index, samples, rate):
    frame = Image.new("RGB", (256, 256), "#101c30")
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((45, 28, 211, 206), radius=55, fill="#4365cc")
    draw.ellipse((78, 80, 94, 100), fill="white")
    draw.ellipse((162, 80, 178, 100), fill="white")
    chunk = samples[int(index * rate / 12) : int((index + 1) * rate / 12)]
    amplitude = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0
    opening = 3 + min(28, int(amplitude * 140))
    draw.ellipse((100, 143 - opening, 156, 143 + opening), fill="#101c30")
    draw.text((85, 222), "ANANTA | AI", fill="white")
    return frame


def avatar(audio, samples, rate, duration, directory):
    """Procedural, visibly synthetic face; amplitude animation, not lip synthesis."""
    return encode_frames(
        audio,
        duration,
        directory,
        frame_source=lambda index: _frame(index, samples, rate),
        require_current=lambda: None,
    )
