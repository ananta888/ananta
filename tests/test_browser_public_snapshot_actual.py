"""Opt-in real network-less source snapshot; not yet a Meet receiver gate."""

import os

import pytest

from tests.meet_private_browser_container import run_private_browser_probe


@pytest.mark.skipif(os.environ.get("BROWSER_PUBLIC_VIEW_GATE") != "1", reason="explicit private sanitized-view probe")
@pytest.mark.timeout(90)
def test_actual_script_disabled_chromium_snapshot_bounds_and_secret_exclusion():
    result = run_private_browser_probe(
        os.environ.get("MEET_TEST_BROWSER_IMAGE", ""),
        "tests.browser_public_snapshot_scenario",
        (
            "tests/browser_public_snapshot_scenario.py",
            "ananta_contracts/browser_public_view.py",
            "worker/meet_media/browser_public_snapshot.py",
        ),
    )
    assert result == {
        "checks": 33,
        "sandbox": True,
        "page_scripts": False,
        "network": False,
        "synthetic_markers": True,
        "production_evidence": False,
    }


@pytest.mark.skipif(os.environ.get("BROWSER_PUBLIC_VIEW_GATE") != "1", reason="explicit private sanitized-view probe")
@pytest.mark.timeout(90)
def test_actual_continuous_sanitized_renderer_omits_source_pixels_and_fences_pending_frames(record_property):
    result = run_private_browser_probe(
        os.environ.get("MEET_TEST_BROWSER_IMAGE", ""),
        "tests.browser_public_renderer_scenario",
        (
            "tests/browser_public_renderer_scenario.py",
            "ananta_contracts/browser_public_view.py",
            "ananta_contracts/browser_view_generation.py",
            "worker/meet_media/browser_public_snapshot.py",
            "worker/meet_media/browser_public_renderer.py",
            "worker/meet_media/browser_public_view_page.py",
        ),
    )
    assert result["frames"] >= 4 and result["distinct_frames"] >= 3 and 0 < result["maximum_bytes"] <= 262144
    assert result["sandbox"] is True and result["source_pixels_published"] is False
    assert result["synthetic_markers"] is True and result["production_evidence"] is False
    assert result["network"] is False and result["bounded_stop_cases"] == 4
    record_property("sanitized_private_renderer", result)


@pytest.mark.skipif(os.environ.get("BROWSER_PUBLIC_VIEW_GATE") != "1", reason="explicit private sanitized-view probe")
@pytest.mark.timeout(90)
def test_actual_script_disabled_workspace_never_exposes_raw_page_and_fences_every_source_change(record_property):
    result = run_private_browser_probe(
        os.environ.get("MEET_TEST_BROWSER_IMAGE", ""),
        "tests.browser_public_workspace_scenario",
        (
            "tests/browser_public_workspace_scenario.py",
            "ananta_contracts/browser_public_view.py",
            "ananta_contracts/browser_view_generation.py",
            "ananta_contracts/browser_public_fetch.py",
            "ananta_contracts/browser_navigation_target.py",
            "worker/meet_media/browser_public_snapshot.py",
            "worker/meet_media/browser_public_renderer.py",
            "worker/meet_media/browser_public_view_page.py",
            "worker/meet_media/browser_public_workspace.py",
        ),
    )
    assert result["frames"] >= 3 and result["distinct_frames"] >= 3
    assert result["bounded_stop_cases"] == 7 and result["sandbox"] is True
    assert result["page_scripts"] is False and result["source_pixels_published"] is False
    assert result["network"] is False and result["synthetic_markers"] is True
    assert result["production_evidence"] is False
    record_property("sanitized_private_workspace", result)
