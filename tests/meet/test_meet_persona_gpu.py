"""Explicit local NVENC gate; synthetic pixels/silence never prove live publication."""

import json
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("MEET_MEDIA_GPU_GATE") != "1", reason="opt-in provisioned local RTX media worker")
@pytest.mark.timeout(50)
def test_real_persona_image_nvenc_renderer():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "40",
            "python",
            "-m",
            "worker.meet_media.persona_video_smoke",
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "renderer": "persona-image-h264_nvenc",
        "production_release_evidence": False,
    }
