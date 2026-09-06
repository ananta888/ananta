"""Synthetic moving clip import in the private worker, never release evidence."""

import hashlib
import json
import time
from pathlib import Path

from ananta_contracts.persona_video import MAX_INPUT_BYTES
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner
from worker.meet_media.persona_video_inspector import PersonaVideoInspector


def command(argv, content=b"", maximum=MAX_INPUT_BYTES):
    result = BoundedSubprocessRunner().run(
        ["/usr/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *argv],
        input_payload=content,
        max_stdout_bytes=maximum,
        timeout_seconds=5,
        cwd=Path(__file__).resolve().parent,
    )
    if result.returncode:
        raise ValueError("persona_video_probe_command_failed")
    return result.stdout


def synthetic_clip():
    # Built-in synthetic generators only; no camera, microphone or external URL.
    source = command(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=22050:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
    )
    return source, PersonaVideoInspector(
        require_current=lambda: None, deadline_monotonic=time.monotonic() + 25
    ).inspect(source, "video/mp4")


def run():
    started = time.monotonic()
    source, value = synthetic_clip()
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
            "-sn",
            "-dn",
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        value.video,
        120 * 256 * 256 * 3,
    )
    stride = 256 * 256 * 3
    if len(raw) != value.frames * stride or raw[:stride] == raw[-stride:]:
        raise ValueError("persona_video_motion_decode_failed")
    return {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "decoded_frames": value.frames,
        "duration_ms": value.duration_ms,
        "source_bytes": len(source),
        "normalized_bytes": len(value.video),
        "preview_bytes": len(value.preview),
        "moving_motif_decoded": True,
        "first_frame_sha256": hashlib.sha256(raw[:stride]).hexdigest(),
        "last_frame_sha256": hashlib.sha256(raw[-stride:]).hexdigest(),
        "source_audio_removed": True,
        "human_capture_used": False,
        "live_delivery_verified": False,
        "production_release_evidence": False,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


if __name__ == "__main__":
    print(json.dumps(run()))
