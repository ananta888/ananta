from datetime import date

from scripts.run_sfu_broadcast_game_day_gate import evaluate as evaluate_game_day
from scripts.run_sfu_broadcast_supply_chain_gate import evaluate as evaluate_supply_chain
from scripts.sfu_broadcast_release_common import canonical_sha256


def test_supply_chain_rejects_unknown_license_and_empty_delta() -> None:
    policy = {
        "required_image_ids": ["sfu"],
        "required_components": ["sfu"],
        "allowed_sbom_formats": ["spdx-2.3"],
        "maximum_parent_age_days": 30,
        "required_container_controls": ["non_root"],
    }
    digest = "a" * 64
    bindings = {
        "source_sha256": digest,
        "config_sha256": canonical_sha256(policy),
        "lockfile_sha256": digest,
        "infrastructure_sha256": digest,
        "image_digests": {"sfu": digest},
    }
    parent_sbom = {"produced_at": "2026-07-01T00:00:00Z"}
    sbom = {
        "schema": "ananta.sfu-broadcast-child-sbom.v1",
        "format": "spdx-2.3",
        "bindings": bindings,
        "components": [{
            "component_id": "sfu", "package_count": 1,
            "unknown_license_count": 1, "floating_reference": False,
            "deployed_digest": digest,
        }],
        "child_delta": {
            "parent_sbom_sha256": canonical_sha256(parent_sbom),
            "component_ids": [], "delta_sha256": digest,
        },
    }
    scans = {
        "schema": "ananta.sfu-broadcast-child-security-scan.v1",
        "bindings": bindings,
        "critical_open": 0, "high_open": 0,
        "malware_detected": 0, "secrets_detected": 0,
        "provenance": [{
            "component_id": "sfu", "signature_verified": True,
            "builder_verified": True, "subject_sha256": digest,
        }],
        "exceptions": [],
    }
    containers = {
        "schema": "ananta.sfu-broadcast-container-controls.v1",
        "bindings": bindings,
        "components": [{
            "component_id": "sfu", "non_root": True,
            "capabilities_added": [],
            "seccomp": {"available": False, "enforced": False},
            "apparmor": {"available": False, "enforced": False},
            "forbidden_responsibilities": [],
        }],
    }
    report = evaluate_supply_chain(
        policy, sbom=sbom, scans=scans, containers=containers,
        parent_sbom=parent_sbom, as_of=date(2026, 7, 22),
    )
    assert "supply_chain_unknown_license" in report["reason_codes"]
    assert "supply_chain_child_delta_empty" in report["reason_codes"]


def test_game_day_cannot_activate_under_parent_no_go() -> None:
    profile = {
        "required_image_ids": [], "scenarios": [],
        "rollback_invariants": [], "cleanup_invariants": [],
        "limits": {"rollback_commit_milliseconds_max": 1},
    }
    digest = "b" * 64
    evidence = {
        "schema": "ananta.sfu-broadcast-real-game-day-result.v1",
        "status": "passed", "real_execution": True, "mock_used": False,
        "bindings": {
            "source_sha256": digest, "config_sha256": canonical_sha256(profile),
            "infrastructure_sha256": digest, "image_digests": {},
        },
        "scenarios": [],
    }
    parent = {
        "decision": "no_go", "rollout_stage": "observe_only",
        "source_sha256": digest,
    }
    report = evaluate_game_day(
        profile, evidence, parent=parent, operator_approved=True,
    )
    assert report["status"] == "failed"
    assert report["activation_allowed"] is False
