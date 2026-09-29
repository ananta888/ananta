"""Opt-in real local CUDA recognition, explicitly synthetic and fully headless."""

import json
import os
import subprocess

import pytest

from worker.meet_media.asr_model import REVISION

pytestmark = [
    pytest.mark.timeout(100),
    pytest.mark.skipif(os.environ.get("MEET_ASR_GPU_GATE") != "1", reason="explicit isolated GPU probe not enabled"),
]


def test_synthetic_piper_audio_is_recognized_by_local_cuda_whisper():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "90",
            "python",
            "-m",
            "worker.meet_media.asr_smoke",
        ],
        capture_output=True,
        timeout=95,
        check=False,
    )
    assert result.returncode == 0, "isolated ASR smoke failed; inspect the dedicated worker locally"
    report = json.loads(result.stdout.decode().strip().splitlines()[-1])
    assert report["status"] == "passed" and report["engine"] == "faster-whisper-cuda"
    assert report["model_revision"] == REVISION
    assert report["classification"] == "synthetic_local_technical_observation"
    assert report["production_release_evidence"] is False and report["real_meet_receive_verified"] is False
    assert report["matched_expected_words"] >= 3
