"""Friendly snake avatar renderer with speech-synced mouth and body language.

Renders 256x256 RGB frames from ``FramePose`` values (mouth opening from the
published speech envelope, blinks, head/body motion from the avatar state) and
encodes a muted, video-only H.264 MP4 at 12 fps so the room client's
persona-video-v1 decoder can loop or hold it.

The renderer only draws; timing lives in ``snake_avatar_timeline`` and state
in ``snake_avatar_state`` (SRP). Encoding goes through an injectable encoder
port so tests never need FFmpeg (DIP).
"""

import hashlib
import os
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from worker.meet_media.snake_avatar_state import IDLE, SPEAKING, THINKING, AvatarStateMachine
from worker.meet_media.snake_avatar_timeline import (
    FPS,
    MAX_CLIP_FRAMES,
    AnimationController,
    FramePose,
    SpeechMediaTimeline,
)

SIZE = 256
MAX_FRAMES = MAX_CLIP_FRAMES

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
_THOUGHT = "#c9e7d1"
_MAX_MOUTH_PX = 24


def _shift(box, dx, dy):
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def render_pose(pose):
    """Draw one frame for ``pose``; deterministic for equal poses."""
    if not isinstance(pose, FramePose):
        raise ValueError("meet_avatar_pose_invalid")
    img = Image.new("RGB", (SIZE, SIZE), _BG)
    draw = ImageDraw.Draw(img)
    body_dy = int(round(pose.body_dy))
    head_dx = int(round(pose.head_dx))
    head_dy = int(round(pose.head_dy)) + body_dy

    # Coiled body resting on the ground, breathing gently.
    draw.ellipse(_shift((24, 158, 232, 252), 0, body_dy), fill=_OUTLINE)
    draw.ellipse(_shift((30, 162, 226, 246), 0, body_dy), fill=_BODY)
    draw.ellipse(_shift((52, 178, 204, 232), 0, body_dy), fill=_BODY_LIGHT)
    draw.ellipse(_shift((86, 194, 170, 226), 0, body_dy), fill=_BODY)

    # Neck rising to the head follows the head offset.
    draw.polygon(
        ((116, 150 + body_dy), (140, 150 + body_dy), (152 + head_dx, 96 + head_dy), (104 + head_dx, 96 + head_dy)),
        fill=_BODY,
    )

    # Head layer is drawn separately so tilt never distorts the body.
    head = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hd = ImageDraw.Draw(head)
    hd.ellipse((70, 37, 186, 137), fill=_OUTLINE)
    hd.ellipse((75, 42, 181, 132), fill=_HEAD)
    hd.ellipse((88, 55, 168, 118), fill=_HEAD_LIGHT)

    # Friendly eyes: pupils follow the gaze, eyelids close on blink.
    gaze_dx, gaze_dy = int(round(pose.gaze_dx)), int(round(pose.gaze_dy))
    for cx in (108, 148):
        hd.ellipse((cx - 12, 60, cx + 12, 86), fill=_EYE)
        hd.ellipse((cx - 5 + gaze_dx, 66 + gaze_dy, cx + 5 + gaze_dx, 80 + gaze_dy), fill=_PUPIL)
        hd.ellipse((cx - 2 + gaze_dx, 68 + gaze_dy, cx + 2 + gaze_dx, 72 + gaze_dy), fill=_EYE)
        if pose.blink > 0.0:
            lid = int(round(26 * min(1.0, pose.blink)))
            hd.rectangle((cx - 13, 59, cx + 13, 59 + lid), fill=_HEAD)
            hd.line((cx - 12, 59 + lid, cx + 12, 59 + lid), fill=_OUTLINE, width=2)

    # Nostrils.
    hd.ellipse((120, 92, 124, 96), fill=_OUTLINE)
    hd.ellipse((132, 92, 136, 96), fill=_OUTLINE)

    # Smiling mouth that opens continuously with the speech envelope.
    opening = int(round(_MAX_MOUTH_PX * min(1.0, max(0.0, pose.mouth))))
    hd.arc((98, 98, 158, 126), start=25, end=155, fill=_OUTLINE, width=3)
    if opening > 0:
        hd.ellipse((110, 100, 146, 100 + opening), fill=_MOUTH)
        tongue_bottom = 100 + max(6, opening - 4)
        if opening > 10 and tongue_bottom > 106:
            hd.ellipse((118, 106, 138, tongue_bottom), fill=_TONGUE)

    if pose.head_tilt:
        head = head.rotate(pose.head_tilt, resample=Image.BILINEAR, center=(128, 96))
    img.paste(head, (head_dx, head_dy), head)

    if pose.state == THINKING:
        # A small thought bubble makes "thinking" recognizable in a tile.
        for box in ((188, 60, 198, 70), (200, 40, 216, 56), (214, 12, 246, 40)):
            draw.ellipse(box, fill=_THOUGHT, outline=_OUTLINE)

    draw.text((92, 232), "ai-snake", fill=_OUTLINE)
    return img


def _speaking_controller(samples, rate, state):
    timeline = SpeechMediaTimeline(samples, rate)
    machine = AvatarStateMachine(initial=IDLE)
    if state != IDLE:
        machine.transition(state, at=0.0)
    return AnimationController(timeline, machine, seed=hashlib.sha256(timeline.samples.tobytes()).hexdigest()[:16])


def snake_frame(index, samples, rate, *, state=SPEAKING):
    """Compatibility: one frame whose mouth follows the envelope of ``samples``."""
    controller = _speaking_controller(np.asarray(samples, dtype=np.float32), int(rate), state)
    poses = controller.poses(start_frame=index, count=1)
    return render_pose(poses[0])


def ffmpeg_encoder(raw_path, video_path, frames):
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


def build_video(samples, rate, seconds, directory, *, state=SPEAKING, start_frame=0, frames=None, encoder=None):
    """Encode a muted video-only 256x256 H.264 MP4; returns (bytes, frames).

    ``start_frame``/``frames`` select one clip segment of the shared timeline
    so long answers can be published as consecutive, still-synchronized clips.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    samples = np.asarray(samples, dtype=np.float32)
    controller = _speaking_controller(samples, int(rate), state)
    if frames is None:
        frames = int(round(max(0.2, float(seconds)) * FPS))
    frames = max(2, min(MAX_FRAMES, int(frames)))
    if type(start_frame) is not int or start_frame < 0:
        raise ValueError("meet_snake_frame_invalid")
    raw_path = directory / "snake.rgb"
    video_path = directory / "snake.mp4"
    with raw_path.open("wb") as raw:
        for pose in controller.poses(start_frame=start_frame, count=frames):
            raw.write(render_pose(pose).tobytes())
    (encoder if encoder is not None else ffmpeg_encoder)(raw_path, video_path, frames)
    data = video_path.read_bytes()
    if len(data) < 16 or data[4:8] != b"ftyp":
        raise ValueError("meet_snake_video_invalid")
    return data, frames


PORTRAIT_SIZE = 512


def render_portrait(size=PORTRAIT_SIZE):
    """Static, front-facing head portrait for a lip-sync reference image.

    The head fills the frame and the closed mouth sits in the lower half
    (about 67 % down), where face-driven lip-sync models such as MuseTalk
    expect the mouth region of a centered face crop. Deterministic.
    """
    if type(size) is not int or not 128 <= size <= 1024:
        raise ValueError("meet_avatar_portrait_size_invalid")
    scale = size / PORTRAIT_SIZE

    def box(x0, y0, x1, y1):
        return tuple(int(round(value * scale)) for value in (x0, y0, x1, y1))

    img = Image.new("RGB", (size, size), _BG)
    draw = ImageDraw.Draw(img)
    # Coils peek in at the bottom so the portrait still reads as the snake.
    draw.ellipse(box(40, 430, 472, 560), fill=_OUTLINE)
    draw.ellipse(box(48, 438, 464, 552), fill=_BODY)
    draw.ellipse(box(120, 456, 392, 540), fill=_BODY_LIGHT)
    neck = ((196, 470), (316, 470), (300, 360), (212, 360))
    draw.polygon([box(x, y, 0, 0)[:2] for x, y in neck], fill=_BODY)

    # Head: outline, base and highlight, same palette as the animated frames.
    draw.ellipse(box(88, 72, 424, 408), fill=_OUTLINE)
    draw.ellipse(box(100, 84, 412, 396), fill=_HEAD)
    draw.ellipse(box(136, 120, 376, 320), fill=_HEAD_LIGHT)

    # Eyes at ~38 % height, straight friendly gaze.
    for cx in (196, 316):
        draw.ellipse(box(cx - 36, 160, cx + 36, 236), fill=_EYE)
        draw.ellipse(box(cx - 15, 178, cx + 15, 220), fill=_PUPIL)
        draw.ellipse(box(cx - 6, 184, cx + 6, 196), fill=_EYE)

    # Nostrils.
    draw.ellipse(box(236, 268, 248, 280), fill=_OUTLINE)
    draw.ellipse(box(264, 268, 276, 280), fill=_OUTLINE)

    # Closed, smiling mouth in the lower half of the portrait.
    draw.arc(box(172, 296, 340, 372), start=20, end=160, fill=_OUTLINE, width=int(max(3, round(6 * scale))))
    draw.text(box(216, 476, 0, 0)[:2], "ai-snake", fill=_OUTLINE)
    return img


def portrait_png(size=PORTRAIT_SIZE):
    """PNG bytes of ``render_portrait``."""
    from io import BytesIO

    buffer = BytesIO()
    render_portrait(size).save(buffer, format="PNG")
    return buffer.getvalue()


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
