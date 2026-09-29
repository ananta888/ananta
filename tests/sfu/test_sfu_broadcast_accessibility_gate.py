from datetime import date

from scripts.e2e.sfu_broadcast_accessibility_e2e import evaluate
from scripts.sfu_broadcast_release_common import canonical_sha256


def test_accessibility_gate_rejects_viewport_emulation_as_real_browser() -> None:
    profile = {
        "required_image_ids": [],
        "required_browsers": ["chromium", "firefox"],
        "required_checks": [],
        "responsive_checks": [],
        "required_started_components": [],
        "required_cleanup_components": [],
        "cleanup_scenarios": [],
        "axe": {"package": "@axe-core/playwright"},
        "screen_reader": {"accepted_automation": ["nvda", "voiceover"]},
        "limits": {"browser_processes_min": 2, "memory_bytes_max": 1024},
    }
    digest = "a" * 64
    evidence = {
        "schema": "ananta.sfu-broadcast-real-accessibility-result.v1",
        "status": "passed",
        "bindings": {
            "source_sha256": digest,
            "config_sha256": canonical_sha256(profile),
            "infrastructure_sha256": digest,
            "image_digests": {},
        },
        "execution": {
            "mock_used": False,
            "real_media": True,
            "browser_processes": [
                {
                    "browser": "chromium",
                    "real_process": True,
                    "viewport_emulation_only": False,
                },
                {
                    "browser": "firefox",
                    "real_process": True,
                    "viewport_emulation_only": True,
                },
            ],
        },
        "checks": {},
        "responsive": {},
        "axe": {"package": "@axe-core/playwright", "violations": 0, "exceptions": []},
        "lifecycle": {"started": {}, "cleanup": {}},
        "screen_reader": {"compatibility_claimed": False, "status": "not_claimed"},
        "resource_peaks": {"memory_bytes": 512},
    }
    report = evaluate(profile, evidence, as_of=date(2026, 7, 22))
    assert "accessibility_browser_matrix_incomplete" in report["reason_codes"]


def test_accessibility_gate_rejects_unverified_screen_reader_claim() -> None:
    profile = {
        "required_image_ids": [],
        "required_browsers": [],
        "required_checks": [],
        "responsive_checks": [],
        "required_started_components": [],
        "required_cleanup_components": [],
        "cleanup_scenarios": [],
        "axe": {"package": "@axe-core/playwright"},
        "screen_reader": {"accepted_automation": ["nvda", "voiceover"]},
        "limits": {"browser_processes_min": 0, "memory_bytes_max": 1024},
    }
    digest = "a" * 64
    evidence = {
        "schema": "ananta.sfu-broadcast-real-accessibility-result.v1",
        "status": "passed",
        "bindings": {
            "source_sha256": digest,
            "config_sha256": canonical_sha256(profile),
            "infrastructure_sha256": digest,
            "image_digests": {},
        },
        "execution": {"mock_used": False, "real_media": True, "browser_processes": []},
        "checks": {},
        "responsive": {},
        "axe": {"package": "@axe-core/playwright", "violations": 0, "exceptions": []},
        "lifecycle": {"started": {}, "cleanup": {}},
        "screen_reader": {
            "compatibility_claimed": True,
            "automation": "simulated",
            "real_device_or_desktop": False,
            "evidence_verified": False,
        },
        "resource_peaks": {"memory_bytes": 512},
    }
    report = evaluate(profile, evidence, as_of=date(2026, 7, 22))
    assert "accessibility_screen_reader_claim_unverified" in report["reason_codes"]
