"""Opt-in real Chromium check; the Meet API itself is a private test double."""

import json
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("MEET_BROWSER_PROBE_GATE") != "1", reason="opt-in private browser container")
@pytest.mark.timeout(40)
def test_pending_real_browser_promises_remain_lease_interruptible():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "ananta-meet-media-meet-media-worker-1",
            "timeout",
            "25",
            "python",
            "-m",
            "worker.meet_media.publication_smoke",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert set(report["pending_operation_stop_ms"]) == {"join", "publish", "leave"}
    assert all(0 < elapsed <= 1500 for elapsed in report["pending_operation_stop_ms"].values())
    assert not report["human_capture_used"] and not report["meet_delivery_verified"]
    assert report["production_release_evidence"] is False
