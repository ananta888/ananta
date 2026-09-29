from scripts.sfu_broadcast_release_common import canonical_sha256
from scripts.validate_sfu_broadcast_nightly_evidence import evaluate


def test_two_hour_soak_cannot_be_claimed_by_a_short_or_mock_run() -> None:
    profile = {
        "required_image_ids": [],
        "kinds": {
            "soak": {
                "receiver_processes_min": 3,
                "duration_seconds_min": 7200,
            }
        },
        "required_safety": ["cleanup_complete"],
        "limits": {
            "timeout_seconds_max": 10800,
            "memory_bytes_max": 1024,
        },
    }
    digest = "a" * 64
    evidence = {
        "schema": "ananta.sfu-broadcast-real-nightly-result.v1",
        "kind": "soak",
        "status": "passed",
        "real_execution": False,
        "mock_used": True,
        "bindings": {
            "source_sha256": digest,
            "config_sha256": canonical_sha256(profile),
            "infrastructure_sha256": digest,
            "image_digests": {},
        },
        "metrics": {
            "receiver_processes": 3,
            "duration_seconds": 7199,
        },
        "safety": {"cleanup_complete": True},
        "resource_peaks": {"memory_bytes": 512, "elapsed_seconds": 7199},
    }
    report = evaluate(profile, evidence, kind="soak")
    assert "nightly_gate_real_execution_missing" in report["reason_codes"]
    assert "nightly_gate_duration_seconds_insufficient" in report["reason_codes"]
