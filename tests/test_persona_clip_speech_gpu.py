"""Opt-in real locally generated speech with bounded imported-clip rendering."""

import json
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("MEET_MEDIA_GPU_GATE") != "1", reason="opt-in private CUDA/NVENC worker")
@pytest.mark.timeout(55)
def test_real_imported_moving_clip_and_speech_pass_decoded_av_gate():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "45",
            "python",
            "-m",
            "worker.meet_media.persona_clip_speech_smoke",
        ],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0, "private clip/speech GPU gate failed"
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "passed" and report["moving_motif_decoded"]
    assert report["repeat_mode"] == "loop" and report["speech_samples"] > 22050
    assert report["video_frames_decoded"] > 12 and report["audio_frames_decoded"] > 1
    assert report["end_skew_us"] <= 150000 and 0 <= report["audio_padding_samples"] < 1024
    assert not report["human_capture_used"] and not report["production_release_evidence"]
    assert not report["live_delivery_verified"] and not report["generative_video_quality_verified"]
