"""Opt-in private codec/timebase gate with real generated audio and video."""

import json
import os
import subprocess

import pytest


@pytest.mark.skipif(
    os.environ.get("MEET_AV_QUALITY_GPU_GATE") != "1", reason="opt-in private provisioned CUDA/NVENC worker"
)
@pytest.mark.timeout(55)
def test_real_generated_mp4_decodes_within_the_declared_av_clock_budget():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "45",
            "python",
            "-m",
            "worker.meet_media.av_quality_smoke",
        ],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0, "private A/V decode quality gate failed"
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "passed" and report["video_frames_decoded"] > 1
    assert report["audio_frames_decoded"] > 1 and report["end_skew_us"] <= 150_000
    assert 0 <= report["audio_padding_samples"] < 1024
    assert not report["live_delivery_verified"] and not report["human_capture_used"]
    assert not report["production_release_evidence"]
