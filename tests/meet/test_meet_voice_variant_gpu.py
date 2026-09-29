"""Opt-in real local CUDA probe, not Hub persona approval or Meet delivery."""

import os

import pytest

from ananta_contracts.meet_voice_catalog import EMOTIONAL_MODEL
from tests.meet_gpu_source_fixture import run_probe


@pytest.mark.timeout(90)
@pytest.mark.skipif(
    os.environ.get("MEET_VOICE_VARIANT_GPU_GATE") != "1", reason="explicit local GPU voice variant gate"
)
def test_actual_pinned_local_voice_variants_and_checkpoint_stop(record_property):
    report = run_probe("voice_variant_smoke")
    assert report["engine"] == "piper-cuda" and report["sample_rate"] == 22050
    assert report["meet_delivery_verified"] is False
    assert [value["speaker_id"] for value in report["variants"]] == [4, 7]
    for value in report["variants"]:
        assert value["model_sha256"] == EMOTIONAL_MODEL.model_sha256
        assert value["config_sha256"] == EMOTIONAL_MODEL.config_sha256
        assert 441 < value["samples"] <= 220500 and value["peak"] > 0.01
        assert value["local_checkpoint_cancel_ms"] < 1000
    record_property("local_voice_variants", report)
