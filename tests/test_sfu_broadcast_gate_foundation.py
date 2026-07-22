from __future__ import annotations

import json
from pathlib import Path

from scripts.e2e.sfu_broadcast_browser_e2e import validate_matrix
from scripts.e2e.sfu_broadcast_harness import (
    DEFAULT_PROFILE,
    build_manifest,
    environment_digests,
    evaluate_real_media_result,
    load_acceptance_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def test_plan_is_reproducible_but_never_claims_external_pass() -> None:
    profile = load_acceptance_profile(DEFAULT_PROFILE)
    first = build_manifest(profile=profile)
    second = build_manifest(profile=profile)

    assert first == second
    assert first["status"] == "blocked"
    assert first["release_blocking"] is True
    assert first["claims"] == {
        "real_browser_verified": False,
        "real_sfu_turn_verified": False,
        "real_media_plane_fuzz_verified": False,
        "playwright_webkit_claimed_as_real_safari": False,
    }
    rendered = json.dumps(first)
    assert "SRC_" not in rendered and "RUN_" not in rendered


def test_mocked_media_result_cannot_satisfy_real_media_adapter_boundary() -> None:
    profile = load_acceptance_profile(DEFAULT_PROFILE)
    digests = environment_digests(profile)
    result = {
        "schema": "ananta.sfu-broadcast-real-media-result.v1",
        "profile_id": profile.profile_id,
        "scenario_version": profile.scenario_version,
        "seed": profile.seeds[0],
        "started_at": "2026-07-22T10:00:00Z",
        "ended_at": "2026-07-22T10:01:00Z",
        "adapter": {
            "contract": "ananta.sfu-broadcast-real-media-adapter.v1",
            "real_media_processes": False,
            "mocked_webrtc": True,
            "mocked_sfu": True,
            "mocked_turn": True,
        },
        "environment_digests": digests,
        "faults": [],
        "measurements": {},
        "cleanup": {},
        "privacy_scan": {"decision": "block", "finding_count": 0},
    }
    reasons = evaluate_real_media_result(result, profile=profile, expected_digests=digests)
    assert "real_media_adapter_attestation_invalid" in reasons
    assert "real_media_measurements_incomplete" in reasons
    assert "real_media_cleanup_incomplete" in reasons


def test_browser_matrix_never_substitutes_webkit_or_viewport_for_real_safari() -> None:
    matrix = json.loads(
        (ROOT / "config/test-profiles/sfu-broadcast/browser-matrix.v1.json").read_text(encoding="utf-8")
    )
    assert validate_matrix(matrix) == ()
    matrix["combinations"][2]["evidence_source"] = "playwright_webkit"
    assert "simulated_safari_claim_forbidden" in validate_matrix(matrix)

