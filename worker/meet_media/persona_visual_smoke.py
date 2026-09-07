"""Real clip-assignment renderer probe; synthetic, not a production Hub dispatch."""

import json
import tempfile
import time
from pathlib import Path

from ananta_contracts.persona_video import encode_video
from worker.meet_media.av_quality import verify_encoded_media
from worker.meet_media.contract import SCHEMA, validate_turn
from worker.meet_media.persona_clip_frames import FRAME_BYTES
from worker.meet_media.persona_clip_smoke import command, synthetic_clip
from worker.meet_media.persona_visual import render_visual
from worker.meet_media.speech import speech


def run():
    started = time.monotonic()
    deadline = started + 40

    def current():
        if time.monotonic() >= deadline:
            raise ValueError("meet_visual_probe_expired")

    _, clip = synthetic_clip()
    reference = {
        "tenant_id": "synthetic",
        "project_id": "synthetic",
        "artifact_id": "synthetic-clip",
        "revision": 1,
        "sha256": clip.video_sha256,
        "kind": "video",
        "classification": "test_only",
    }
    turn = {
        "schema": SCHEMA,
        "task_id": "synthetic-probe",
        "lease_id": "synthetic-probe-lease",
        "tenant_id": "synthetic",
        "project_id": "synthetic",
        "deadline": int(time.time()) + 40,
        "text": "Hallo, dieser gespeicherte Clip begleitet die lokale Sprachausgabe.",
        "persona_video": {
            "reference": reference,
            "clip": encode_video(clip),
            "origin_kind": "upload",
            "repeat_mode": "loop",
        },
    }
    validate_turn(turn, time.time())
    with tempfile.TemporaryDirectory(prefix="meet-visual-probe-") as temporary:
        directory = Path(temporary)
        audio = directory / "speech.wav"
        samples, _, duration = speech(turn["text"], audio, require_current=current)
        video, engine, selected = render_visual(turn, audio, duration, directory, require_current=current)
        if engine != "persona-clip-h264_nvenc" or selected != {"persona_video": reference}:
            raise ValueError("meet_visual_probe_selection_failed")
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
            raise ValueError("meet_visual_probe_motion_failed")
        current()
    return report | {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "renderer": engine,
        "speech_samples": len(samples),
        "moving_motif_decoded": True,
        "human_capture_used": False,
        "production_release_evidence": False,
        "live_delivery_verified": False,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


if __name__ == "__main__":
    print(json.dumps(run()))
