"""Opt-in pinned-image GPU checks, no service restart or real receive grant."""

import os

import pytest

from tests.meet_gpu_capacity import require_gpu_capacity
from tests.meet_gpu_source_fixture import run_probe

pytestmark = [
    pytest.mark.timeout(100),
    pytest.mark.skipif(
        os.environ.get("MEET_AUDIO_PROFILE_GPU_GATE") != "1", reason="explicit packaged GPU profile gate not enabled"
    ),
]


@pytest.mark.parametrize("name,vad", [("bounded-4s-vad", "local-vad-v1"), ("bounded-4s-no-vad", "off")])
def test_packaged_cuda_profile_recognizes_exact_four_second_synthetic_segment(name, vad):
    require_gpu_capacity()
    image = os.environ["MEET_AUDIO_PROFILE_IMAGE"]
    report = run_probe("asr_smoke", packaged_image=image, asr_profile=name)
    assert report["engine"] == "faster-whisper-cuda" and report["received_samples"] == 64000
    assert report["audio_profile"] == {
        "schema": "ananta.meet-audio-profile.v1",
        "language": "de",
        "model": "whisper-small-pinned",
        "vad": vad,
        "segment_seconds": 4,
    }
    assert report["matched_expected_words"] >= 3 and report["real_meet_receive_verified"] is False
