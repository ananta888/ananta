"""Actual moving imported clip plus pinned CUDA speech and NVENC output gate."""

import json
import tempfile
import time
from pathlib import Path

from worker.meet_media.av_quality import verify_encoded_media
from worker.meet_media.persona_clip_frames import FRAME_BYTES, render_persona_clip
from worker.meet_media.persona_clip_smoke import command, synthetic_clip
from worker.meet_media.speech import speech


def run():
    started = time.monotonic()
    deadline = started + 40

    def current():
        if time.monotonic() >= deadline:
            raise ValueError("persona_clip_speech_probe_expired")

    _, clip = synthetic_clip()
    with tempfile.TemporaryDirectory(prefix="persona-clip-speech-") as temporary:
        directory = Path(temporary)
        audio = directory / "speech.wav"
        samples, _, duration = speech("Hallo, dieser Clip begleitet Anantas lokale Sprachausgabe.", audio)
        video = render_persona_clip(
            clip,
            audio,
            duration,
            directory,
            origin_kind="upload",
            classification="test_only",
            repeat_mode="loop",
            require_current=current,
            deadline_monotonic=min(deadline, time.monotonic() + 20),
        )
        content = video.read_bytes()
        report = verify_encoded_media(content, expected_samples=len(samples), require_current=current)
        raw = command(
            [
                "-protocol_whitelist",
                "pipe",
                "-f",
                "mov",
                "-threads",
                "1",
                "-i",
                "pipe:0",
                "-map",
                "0:v:0",
                "-an",
                "-threads",
                "1",
                "-frames:v",
                "2",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            content,
            2 * FRAME_BYTES,
        )
        if len(raw) != 2 * FRAME_BYTES or raw[:FRAME_BYTES] == raw[FRAME_BYTES:]:
            raise ValueError("persona_clip_encoded_motion_failed")
        current()
    return report | {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "renderer": "labelled-imported-clip-h264_nvenc",
        "repeat_mode": "loop",
        "moving_motif_decoded": True,
        "speech_samples": len(samples),
        "human_capture_used": False,
        "production_release_evidence": False,
        "generative_video_quality_verified": False,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


if __name__ == "__main__":
    print(json.dumps(run()))
