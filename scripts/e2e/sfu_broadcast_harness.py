#!/usr/bin/env python3
"""Manifest and evidence boundary extending the parent Semantic-SFU harness."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.sfu_broadcast_gate_common import (
        SfuBroadcastGateError,
        atomic_write_report,
        canonical_sha256,
        digest_paths,
        read_bounded_json,
        scan_content_free_document,
    )
except ModuleNotFoundError:
    from sfu_broadcast_gate_common import (  # type: ignore[no-redef]
        SfuBroadcastGateError,
        atomic_write_report,
        canonical_sha256,
        digest_paths,
        read_bounded_json,
        scan_content_free_document,
    )

DEFAULT_PROFILE = ROOT / "config/test-profiles/sfu-broadcast/acceptance.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-acceptance.json"
PROFILE_SCHEMA = "ananta.sfu-broadcast-acceptance-profile.v1"
RESULT_SCHEMA = "ananta.sfu-broadcast-real-media-result.v1"
MANIFEST_SCHEMA = "ananta.sfu-broadcast-acceptance-manifest.v1"
REAL_MEDIA_ADAPTER_CONTRACT = "ananta.sfu-broadcast-real-media-adapter.v1"
SOURCE_PATHS = (
    "agent/services/sfu_broadcast_contract_validator.py",
    "agent/services/sfu_broadcast_privacy_sentinel.py",
    "agent/services/sfu_broadcast_route_port.py",
    "agent/services/sfu_fanout_route_lifecycle.py",
    "agent/services/sfu_broadcast_data_queue_policy.py",
    "scripts/e2e/semantic_sfu_group_e2e.py",
    "scripts/e2e/semantic_sfu_failover_e2e.py",
    "scripts/e2e/sfu_broadcast_harness.py",
    "scripts/run_sfu_broadcast_fuzz_gate.py",
    "scripts/run_sfu_broadcast_security_gate.py",
    "scripts/scan_sfu_broadcast_privacy_sentinels.py",
)
SCHEMA_PATHS = (
    "schemas/webrtc/fanout_route_intent.v1.json",
    "schemas/webrtc/receiver_group_intent.v1.json",
    "schemas/webrtc/receiver_quality_observation.v1.json",
    "schemas/webrtc/fanout_accounting_window.v1.json",
    "schemas/webrtc/browser_media_capability_observation.v1.json",
)


@dataclass(frozen=True, slots=True)
class AcceptanceProfile:
    document: Mapping[str, Any]
    seeds: tuple[int, ...]
    limits: Mapping[str, int]
    required_faults: tuple[str, ...]

    @property
    def profile_id(self) -> str:
        return str(self.document["profile_id"])

    @property
    def scenario_version(self) -> str:
        return str(self.document["scenario_version"])


def load_acceptance_profile(path: Path = DEFAULT_PROFILE) -> AcceptanceProfile:
    document = read_bounded_json(path)
    required = {
        "schema",
        "profile_id",
        "scenario_version",
        "seeds",
        "limits",
        "pinned",
        "parent_harnesses",
        "required_external_capabilities",
        "faults",
        "scenarios",
        "cleanup_assertions",
    }
    if set(document) != required or document.get("schema") != PROFILE_SCHEMA:
        raise SfuBroadcastGateError("acceptance_profile_shape_invalid")
    seeds_value = document.get("seeds")
    if (
        not isinstance(seeds_value, list)
        or not 3 <= len(seeds_value) <= 32
        or any(type(seed) is not int or not 1 <= seed <= 2_147_483_647 for seed in seeds_value)
        or len(set(seeds_value)) != len(seeds_value)
    ):
        raise SfuBroadcastGateError("acceptance_profile_seeds_invalid")
    limits = document.get("limits")
    expected_limits = {
        "overall_timeout_seconds",
        "scenario_timeout_seconds",
        "cleanup_deadline_ms",
        "cpu_seconds_max",
        "memory_bytes_max",
        "child_processes_max",
        "artifact_bytes_max",
        "cases_per_seed_min",
        "seeds_min",
    }
    if not isinstance(limits, Mapping) or set(limits) != expected_limits:
        raise SfuBroadcastGateError("acceptance_profile_limits_invalid")
    if any(type(value) is not int or value < 1 for value in limits.values()):
        raise SfuBroadcastGateError("acceptance_profile_limits_invalid")
    if (
        limits["overall_timeout_seconds"] > 1800
        or limits["scenario_timeout_seconds"] > 300
        or limits["cleanup_deadline_ms"] > 60_000
        or limits["memory_bytes_max"] > 2 * 1024 * 1024 * 1024
        or limits["artifact_bytes_max"] > 64 * 1024 * 1024
        or limits["seeds_min"] > len(seeds_value)
    ):
        raise SfuBroadcastGateError("acceptance_profile_limits_invalid")
    pinned = document.get("pinned")
    if not isinstance(pinned, Mapping) or set(pinned) != {
        "livekit_image",
        "turn_image",
        "browser_lock",
        "compose",
        "livekit_config",
    }:
        raise SfuBroadcastGateError("acceptance_profile_pins_invalid")
    if any("sha256:" not in str(pinned[name]) for name in ("livekit_image", "turn_image")):
        raise SfuBroadcastGateError("acceptance_profile_image_pin_invalid")
    faults = document.get("faults")
    expected_faults = {
        "loss",
        "jitter",
        "reorder",
        "bandwidth",
        "partition",
        "process_kill",
        "clock_skew",
        "cpu_pressure",
        "memory_pressure",
        "disk_failure",
        "database_failure",
    }
    if not isinstance(faults, list) or set(faults) != expected_faults or len(faults) != len(expected_faults):
        raise SfuBroadcastGateError("acceptance_profile_faults_invalid")
    if scan_content_free_document(document):
        raise SfuBroadcastGateError("acceptance_profile_content_boundary_invalid")
    return AcceptanceProfile(
        document=document,
        seeds=tuple(seeds_value),
        limits={str(key): int(value) for key, value in limits.items()},
        required_faults=tuple(sorted(expected_faults)),
    )


def environment_digests(profile: AcceptanceProfile, *, root: Path = ROOT) -> dict[str, object]:
    pinned = profile.document["pinned"]
    return {
        "source_sha256": digest_paths(root, SOURCE_PATHS),
        "schema_sha256": digest_paths(root, SCHEMA_PATHS),
        "config_sha256": canonical_sha256(profile.document),
        "browser_lock_sha256": digest_paths(root, (str(pinned["browser_lock"]),)),
        "infrastructure_sha256": digest_paths(
            root,
            (str(pinned["compose"]), str(pinned["livekit_config"])),
        ),
        "livekit_image": pinned["livekit_image"],
        "turn_image": pinned["turn_image"],
    }


def evaluate_real_media_result(
    result: Mapping[str, Any],
    *,
    profile: AcceptanceProfile,
    expected_digests: Mapping[str, object],
) -> tuple[str, ...]:
    reasons: set[str] = set(scan_content_free_document(result))
    required = {
        "schema",
        "profile_id",
        "scenario_version",
        "seed",
        "started_at",
        "ended_at",
        "adapter",
        "environment_digests",
        "faults",
        "measurements",
        "cleanup",
        "privacy_scan",
    }
    if set(result) != required or result.get("schema") != RESULT_SCHEMA:
        return ("real_media_result_shape_invalid",)
    if result.get("profile_id") != profile.profile_id or result.get("scenario_version") != profile.scenario_version:
        reasons.add("real_media_profile_binding_invalid")
    if result.get("seed") not in profile.seeds:
        reasons.add("real_media_seed_invalid")
    if result.get("environment_digests") != expected_digests:
        reasons.add("real_media_environment_digest_mismatch")
    adapter = result.get("adapter") if isinstance(result.get("adapter"), Mapping) else {}
    if (
        adapter.get("contract") != REAL_MEDIA_ADAPTER_CONTRACT
        or adapter.get("real_media_processes") is not True
        or adapter.get("mocked_webrtc") is not False
        or adapter.get("mocked_sfu") is not False
        or adapter.get("mocked_turn") is not False
    ):
        reasons.add("real_media_adapter_attestation_invalid")
    faults = result.get("faults") if isinstance(result.get("faults"), list) else []
    fault_names = {item.get("name") for item in faults if isinstance(item, Mapping)}
    if fault_names != set(profile.required_faults) or any(
        item.get("seeded") is not True or item.get("reverted") is not True
        for item in faults
        if isinstance(item, Mapping)
    ):
        reasons.add("real_media_fault_coverage_incomplete")
    measurements = result.get("measurements") if isinstance(result.get("measurements"), Mapping) else {}
    if (
        measurements.get("publisher_count") != 1
        or int(measurements.get("receiver_count", 0)) < 3
        or int(measurements.get("real_browser_process_count", 0)) < 4
        or measurements.get("publisher_upstream_peer_connection_count") != 1
        or int(measurements.get("publication_count_per_source_max", 0)) != 1
        or not (
            int(measurements.get("observed_rid_count", 0)) >= 2
            or measurements.get("observed_svc_mode") is True
        )
        or int(measurements.get("private_recovery_cross_receiver_matches", -1)) != 0
    ):
        reasons.add("real_media_measurements_incomplete")
    cleanup = result.get("cleanup") if isinstance(result.get("cleanup"), Mapping) else {}
    for field in profile.document["cleanup_assertions"]:
        expected = True if field == "credentials_invalidated" else 0
        if cleanup.get(field) != expected:
            reasons.add("real_media_cleanup_incomplete")
            break
    privacy = result.get("privacy_scan") if isinstance(result.get("privacy_scan"), Mapping) else {}
    if privacy.get("decision") != "allow" or privacy.get("finding_count") != 0:
        reasons.add("real_media_privacy_scan_failed")
    if not str(result.get("started_at", "")).endswith("Z") or not str(result.get("ended_at", "")).endswith("Z"):
        reasons.add("real_media_run_window_invalid")
    return tuple(sorted(reasons))


def build_manifest(
    *,
    profile: AcceptanceProfile,
    external_result: Mapping[str, Any] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    digests = environment_digests(profile, root=root)
    scenarios = []
    for scenario in profile.document["scenarios"]:
        evidence_class = scenario["evidence_class"]
        scenarios.append(
            {
                "scenario_id": scenario["scenario_id"],
                "evidence_class": evidence_class,
                "status": "unverified" if evidence_class == "local_model" else "blocked",
                "reason_code": (
                    "execution_not_requested"
                    if evidence_class == "local_model"
                    else "external_browser_container_media_evidence_unavailable"
                ),
            }
        )
    reasons = ["external_browser_container_media_evidence_unavailable"]
    status = "blocked"
    run_window = {"started_at": None, "ended_at": None}
    if external_result is not None:
        external_reasons = evaluate_real_media_result(
            external_result,
            profile=profile,
            expected_digests=digests,
        )
        reasons = list(external_reasons)
        status = "passed" if not reasons else "failed"
        run_window = {
            "started_at": external_result.get("started_at"),
            "ended_at": external_result.get("ended_at"),
        }
        for item in scenarios:
            if item["evidence_class"] == "external_real_media":
                item["status"] = status
                item["reason_code"] = reasons[0] if reasons else None
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "profile_id": profile.profile_id,
        "scenario_version": profile.scenario_version,
        "status": status,
        "release_blocking": status != "passed",
        "reason_codes": sorted(set(reasons)),
        "environment_digests": digests,
        "seeds": list(profile.seeds),
        "limits": dict(profile.limits),
        "run_window": run_window,
        "scenarios": scenarios,
        "claims": {
            "real_browser_verified": status == "passed",
            "real_sfu_turn_verified": status == "passed",
            "real_media_plane_fuzz_verified": status == "passed",
            "playwright_webkit_claimed_as_real_safari": False,
        },
    }
    report_reasons = scan_content_free_document(manifest)
    if report_reasons:
        raise SfuBroadcastGateError(report_reasons[0])
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed SFU broadcast acceptance manifest.")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        profile = load_acceptance_profile(args.profile)
        external = read_bounded_json(args.external_result) if args.external_result else None
        manifest = build_manifest(profile=profile, external_result=external)
        atomic_write_report(args.output, manifest)
    except SfuBroadcastGateError as exc:
        print(json.dumps({"status": "failed", "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(json.dumps({"status": manifest["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
