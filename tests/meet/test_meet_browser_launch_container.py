"""Serial owned Chromium starts; no GPU, host profiles, public ports or human input."""

import os
from uuid import uuid4

import pytest

from tests.meet_dialog_browser_fixture import DialogBrowserFixture, docker


@pytest.mark.skipif(os.environ.get("MEET_BROWSER_LAUNCH_GATE") != "1", reason="explicit private browser launch gate")
@pytest.mark.timeout(90)
@pytest.mark.parametrize("attempt", range(3))
def test_actual_sandboxed_browser_starts_with_original_resource_and_time_bounds(attempt, record_property):
    network = "meet-test-tls-" + str(uuid4()) + "-network"
    browser = DialogBrowserFixture(network, 180)
    try:
        docker("network", "create", "--internal", network)
        browser.start("a" * 43 + "=")
        assert browser.endpoint is not None and browser.process_id > 0
        record_property(
            "owned_browser_launch", {"attempt": attempt, "synthetic": True, "production_release_evidence": False}
        )
    finally:
        try:
            browser.close()
        finally:
            docker("network", "rm", network)
