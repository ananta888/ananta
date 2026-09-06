"""Explicit headless hardware gate for real Piper PCM output and local stop."""

import json
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("MEET_SPEECH_GPU_GATE") != "1", reason="opt-in private provisioned CUDA worker")
@pytest.mark.timeout(55)
def test_real_cuda_speech_frames_have_a_bounded_clock_and_stop_without_capture():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "45",
            "python",
            "-m",
            "worker.meet_media.speech_smoke",
        ],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0, "private CUDA speech framing probe failed"
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "passed" and report["engine"] == "piper-cuda"
    assert report["sample_rate"] == 22_050
    assert report["frames"] >= 2 and 0 < report["samples"] <= 220_500
    assert not report["human_capture_used"] and not report["meet_delivery_verified"]
    assert report["production_release_evidence"] is False
