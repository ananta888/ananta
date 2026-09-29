"""Opt-in actual GPU checks against current source without redeploying services."""

import os

import pytest

from tests.meet_gpu_source_fixture import run_probe


@pytest.mark.skipif(os.environ.get("MEET_CURRENT_SOURCE_GPU_GATE") != "1", reason="opt-in isolated local GPU probe")
@pytest.mark.timeout(150)
@pytest.mark.parametrize("module", ["persona_visual_smoke", "speech_smoke"])
def test_current_source_gpu_render_and_speech(module, record_property):
    report = run_probe(module)
    record_property("gpu_probe", report)
    assert report["classification"] == "synthetic_local_technical_observation"
    if module == "persona_visual_smoke":
        assert report["renderer"] == "persona-clip-h264_nvenc"
        assert report["moving_motif_decoded"] is True and report["video_frames_decoded"] > 1
        assert report["audio_frames_decoded"] > 1 and report["live_delivery_verified"] is False
    else:
        assert report["engine"] == "piper-cuda" and report["frames"] > 1
        assert report["sample_rate"] == 22050 and report["meet_delivery_verified"] is False
