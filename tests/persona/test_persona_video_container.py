"""Optional actual CPU decoder gate in the private, provisioned media worker."""

import json
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("PERSONA_VIDEO_CONTAINER_GATE") != "1", reason="opt-in private decoder container")
@pytest.mark.timeout(45)
def test_actual_moving_clip_normalization_removes_audio_and_preserves_motion():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "35",
            "python",
            "-m",
            "worker.meet_media.persona_clip_smoke",
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, "private clip normalization gate failed"
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "passed" and 2 <= report["decoded_frames"] <= 120
    assert report["moving_motif_decoded"] and report["source_audio_removed"]
    assert report["first_frame_sha256"] != report["last_frame_sha256"]
    assert not report["human_capture_used"] and not report["production_release_evidence"]
    assert not report["live_delivery_verified"]
