"""Deterministic frame validation and opt-in real sandboxed browser capability."""

import base64
import io
import json
import os
import subprocess

import pytest
from PIL import Image

from worker.meet_media.browser_source_probe import ProbeFrames


def encoded(color, size=(640, 360), format="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode()


def test_probe_observes_actual_decoded_moving_colors_without_retaining_pixels():
    frames = ProbeFrames()
    for _ in range(10):
        frames.observe(encoded("red"))
        frames.observe(encoded((0, 255, 0)))
    assert frames.count == 20 and frames.colors == {"red", "green"}
    assert set(vars(frames)) == {"count", "bytes", "colors"}


@pytest.mark.parametrize("data", ["bad base64", "A" * 700_001, None])
def test_probe_rejects_unbounded_or_invalid_inputs(data):
    with pytest.raises((ValueError, TypeError)):
        ProbeFrames().observe(data)


@pytest.mark.parametrize("size,format", [((641, 360), "JPEG"), ((640, 360), "PNG")])
def test_probe_does_not_accept_another_surface_format(size, format):
    with pytest.raises(ValueError, match="format_invalid"):
        ProbeFrames().observe(encoded("red", size, format))


@pytest.mark.skipif(
    os.environ.get("MEET_BROWSER_PROBE_GATE") != "1", reason="opt-in private provisioned browser container"
)
@pytest.mark.timeout(45)
def test_real_owned_browser_delivers_continuous_decodable_frames_in_private_container():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "30",
            "python",
            "-m",
            "worker.meet_media.browser_source_probe",
        ],
        capture_output=True,
        text=True,
        timeout=35,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed" and report["sandbox"]
    assert report["frames_decoded"] >= 5 and report["observed_colors"] == ["green", "red"]
    assert not report["human_capture_used"] and not report["meet_delivery_verified"]
    assert report["production_release_evidence"] is False
