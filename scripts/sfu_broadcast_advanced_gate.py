#!/usr/bin/env python3
"""Fail-closed evidence boundary for advanced SFU broadcast gates.

The module deliberately does not implement an in-process simulator. Long-running
browser, SFU, TURN and fault-injection harnesses remain external workers. The hub
runner accepts their bounded result document only after checking process,
capability, environment and cleanup attestations.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

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


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = "ananta.sfu-broadcast-advanced-gate-profile.v1"
ADAPTER_CONTRACT = "ananta.sfu-broadcast-external-gate-adapter.v1"
SUPPORTED_GATES = frozenset(
    {"SFB-GATE-005", "SFB-GATE-006", "SFB-GATE-007", "SFB-GATE-008", "SFB-GATE-010"}
)
SHA256_NAMES = frozenset(
    {
        "source_sha256",
        "config_sha256",
        "image_sha256",
        "infrastructure_sha256",
        "browser_lock_sha256",
    }
)


class ExternalGateResultPort(Protocol):
    """Small adapter port for externally produced real-system observations."""

    def read(self, *, maximum_bytes: int) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class FileExternalGateResultAdapter:
    """Bounded filesystem adapter; validation remains in the domain runner."""

    path: Path

    def read(self, *, maximum_bytes: int) -> Mapping[str, Any]:
        return read_bounded_json(self.path, maximum_bytes=maximum_bytes)


@dataclass(frozen=True, slots=True)
class AdvancedGateProfile:
    document: Mapping[str, Any]

    @property
    def gate_id(self) -> str:
        return str(self.document["gate_id"])

    @property
    def profile_id(self) -> str:
        return str(self.document["profile_id"])

    @property
    def artifact_schema(self) -> str:
        return str(self.document["artifact_schema"])

    @property
    def external_result_schema(self) -> str:
        return str(self.document["external_result_schema"])

    @property
    def execution(self) -> Mapping[str, int]:
        return self.document["execution"]  # type: ignore[return-value]


def _unique_strings(value: Any, *, minimum: int = 1) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= minimum
        and all(isinstance(item, str) and item for item in value)
        and len(set(value)) == len(value)
    )


def _relative_paths(value: Any) -> bool:
    if not _unique_strings(value):
        return False
    for item in value:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            return False
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def load_advanced_gate_profile(path: Path) -> AdvancedGateProfile:
    document = read_bounded_json(path)
    expected = {
        "schema",
        "gate_id",
        "profile_id",
        "artifact_schema",
        "external_result_schema",
        "seed",
        "execution",
        "topology",
        "required_capabilities",
        "required_scenarios",
        "required_metrics",
        "required_assertions",
        "cleanup_requirements",
        "thresholds",
        "environment",
    }
    if set(document) != expected or document.get("schema") != PROFILE_SCHEMA:
        raise SfuBroadcastGateError("advanced_gate_profile_shape_invalid")
    if document.get("gate_id") not in SUPPORTED_GATES:
        raise SfuBroadcastGateError("advanced_gate_profile_gate_invalid")
    if not isinstance(document.get("profile_id"), str) or not document["profile_id"]:
        raise SfuBroadcastGateError("advanced_gate_profile_id_invalid")
    if not all(
        isinstance(document.get(field), str) and str(document[field]).startswith("ananta.")
        for field in ("artifact_schema", "external_result_schema")
    ):
        raise SfuBroadcastGateError("advanced_gate_profile_schema_binding_invalid")
    seed = document.get("seed")
    if type(seed) is not int or not 1 <= seed <= 2_147_483_647:
        raise SfuBroadcastGateError("advanced_gate_profile_seed_invalid")

    execution = document.get("execution")
    execution_fields = {
        "warmup_seconds",
        "measurement_seconds",
        "repetitions",
        "timeout_seconds",
        "cleanup_timeout_seconds",
        "artifact_bytes_max",
    }
    if not isinstance(execution, Mapping) or set(execution) != execution_fields:
        raise SfuBroadcastGateError("advanced_gate_execution_invalid")
    if any(type(execution.get(field)) is not int or int(execution[field]) < 1 for field in execution_fields):
        raise SfuBroadcastGateError("advanced_gate_execution_invalid")
    minimum_timeout = (
        int(execution["warmup_seconds"])
        + int(execution["measurement_seconds"]) * int(execution["repetitions"])
        + int(execution["cleanup_timeout_seconds"])
    )
    if (
        int(execution["timeout_seconds"]) < minimum_timeout
        or int(execution["timeout_seconds"]) > 86_400
        or int(execution["artifact_bytes_max"]) > 64 * 1024 * 1024
    ):
        raise SfuBroadcastGateError("advanced_gate_execution_bounds_invalid")

    if not isinstance(document.get("topology"), Mapping) or not document["topology"]:
        raise SfuBroadcastGateError("advanced_gate_topology_invalid")
    for field in ("required_capabilities", "required_scenarios", "required_metrics", "required_assertions"):
        if not _unique_strings(document.get(field)):
            raise SfuBroadcastGateError("advanced_gate_requirements_invalid")
    cleanup = document.get("cleanup_requirements")
    if not isinstance(cleanup, Mapping) or not cleanup or any(
        not isinstance(key, str) or not key or not isinstance(value, (bool, int))
        for key, value in cleanup.items()
    ):
        raise SfuBroadcastGateError("advanced_gate_cleanup_invalid")

    thresholds = document.get("thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        raise SfuBroadcastGateError("advanced_gate_thresholds_invalid")
    threshold_metrics: set[str] = set()
    for threshold in thresholds:
        if not isinstance(threshold, Mapping) or set(threshold) != {"metric", "operator", "value"}:
            raise SfuBroadcastGateError("advanced_gate_thresholds_invalid")
        metric = threshold.get("metric")
        if (
            not isinstance(metric, str)
            or metric not in document["required_metrics"]
            or metric in threshold_metrics
            or threshold.get("operator") not in {"eq", "gte", "lte"}
            or not _is_number(threshold.get("value"))
        ):
            raise SfuBroadcastGateError("advanced_gate_thresholds_invalid")
        threshold_metrics.add(metric)

    environment = document.get("environment")
    if not isinstance(environment, Mapping) or set(environment) != {
        "source_paths",
        "infrastructure_paths",
        "browser_lock_path",
        "images",
        "required_digest_names",
    }:
        raise SfuBroadcastGateError("advanced_gate_environment_invalid")
    if not _relative_paths(environment.get("source_paths")) or not _relative_paths(
        environment.get("infrastructure_paths")
    ):
        raise SfuBroadcastGateError("advanced_gate_environment_paths_invalid")
    if not _relative_paths([environment.get("browser_lock_path")]):
        raise SfuBroadcastGateError("advanced_gate_environment_paths_invalid")
    images = environment.get("images")
    if not isinstance(images, Mapping) or not images or any(
        not isinstance(name, str) or not isinstance(value, str) or "@sha256:" not in value
        for name, value in images.items()
    ):
        raise SfuBroadcastGateError("advanced_gate_image_pins_invalid")
    if set(environment.get("required_digest_names") or []) != SHA256_NAMES:
        raise SfuBroadcastGateError("advanced_gate_digest_requirements_invalid")

    topology = document["topology"]
    if document["gate_id"] == "SFB-GATE-005":
        if (
            int(execution["measurement_seconds"]) < 7200
            or int(topology.get("rooms_min", 0)) < 3
            or int(topology.get("receivers_per_room_min", 0)) < 10
        ):
            raise SfuBroadcastGateError("soak_profile_minimums_invalid")
    elif document["gate_id"] == "SFB-GATE-006":
        if any(int(topology.get(field, 0)) < 2 for field in ("hub_count", "sfu_runtime_count", "room_count")):
            raise SfuBroadcastGateError("fleet_profile_minimums_invalid")
    elif document["gate_id"] == "SFB-GATE-007":
        if int(topology.get("turn_instance_count", 0)) < 2 or topology.get("relay_policy") != "turn_only":
            raise SfuBroadcastGateError("turn_profile_minimums_invalid")
    elif document["gate_id"] == "SFB-GATE-008":
        if topology.get("receiver_tiers") != [10, 25, 50, 100, 250]:
            raise SfuBroadcastGateError("scale_profile_tiers_invalid")
        if int(topology.get("real_browser_sentinels_min", 0)) < 3:
            raise SfuBroadcastGateError("scale_profile_browser_sentinels_invalid")
        if not _unique_strings(topology.get("required_modes")):
            raise SfuBroadcastGateError("scale_profile_modes_invalid")
    elif document["gate_id"] == "SFB-GATE-010":
        if topology.get("rollout_stages") != ["flag_off", "internal", "cohort", "percent", "released"]:
            raise SfuBroadcastGateError("rollout_profile_stages_invalid")

    if scan_content_free_document(document):
        raise SfuBroadcastGateError("advanced_gate_profile_content_boundary_invalid")
    return AdvancedGateProfile(document=document)


def environment_digests(profile: AdvancedGateProfile, *, root: Path = ROOT) -> dict[str, str]:
    environment = profile.document["environment"]
    return {
        "source_sha256": digest_paths(root, tuple(environment["source_paths"])),
        "config_sha256": canonical_sha256(profile.document),
        "image_sha256": canonical_sha256(environment["images"]),
        "infrastructure_sha256": digest_paths(root, tuple(environment["infrastructure_paths"])),
        "browser_lock_sha256": digest_paths(root, (str(environment["browser_lock_path"]),)),
    }


def build_plan(profile: AdvancedGateProfile, *, digests: Mapping[str, str]) -> dict[str, Any]:
    return {
        "gate_id": profile.gate_id,
        "profile_id": profile.profile_id,
        "seed": profile.document["seed"],
        "execution": dict(profile.execution),
        "topology": dict(profile.document["topology"]),
        "required_capabilities": list(profile.document["required_capabilities"]),
        "required_scenarios": list(profile.document["required_scenarios"]),
        "required_metrics": list(profile.document["required_metrics"]),
        "required_assertions": list(profile.document["required_assertions"]),
        "cleanup_requirements": dict(profile.document["cleanup_requirements"]),
        "environment_digests": dict(digests),
    }


def _threshold_reasons(profile: AdvancedGateProfile, measurements: Mapping[str, Any]) -> set[str]:
    reasons: set[str] = set()
    for threshold in profile.document["thresholds"]:
        metric = str(threshold["metric"])
        observed = measurements.get(metric)
        expected = threshold["value"]
        if not _is_number(observed):
            reasons.add("required_measurement_invalid")
            continue
        operator = threshold["operator"]
        satisfied = (
            (operator == "eq" and float(observed) == float(expected))
            or (operator == "gte" and float(observed) >= float(expected))
            or (operator == "lte" and float(observed) <= float(expected))
        )
        if not satisfied:
            reasons.add(f"threshold_failed:{metric}")
    return reasons


def _scale_reasons(profile: AdvancedGateProfile, result: Mapping[str, Any]) -> set[str]:
    reasons: set[str] = set()
    topology = profile.document["topology"]
    tiers = result.get("tiers")
    if not isinstance(tiers, list):
        return {"scale_tier_evidence_missing"}
    expected_tiers = topology["receiver_tiers"]
    observed_tiers = [item.get("receiver_count") for item in tiers if isinstance(item, Mapping)]
    if observed_tiers != expected_tiers or len(tiers) != len(expected_tiers):
        reasons.add("scale_tier_coverage_invalid")
    failed_seen = False
    for item in tiers:
        if not isinstance(item, Mapping) or set(item) != {
            "receiver_count",
            "status",
            "repetitions",
            "real_browser_sentinels",
            "scenario_modes",
            "measurements",
        }:
            reasons.add("scale_tier_shape_invalid")
            continue
        status = item.get("status")
        if status == "failed":
            failed_seen = True
            reasons.add("scale_tier_failed")
        elif status != "completed":
            reasons.add("scale_tier_status_invalid")
        elif failed_seen:
            reasons.add("scale_tier_order_invalid")
        if item.get("repetitions") != profile.execution["repetitions"]:
            reasons.add("scale_tier_repetitions_invalid")
        if int(item.get("real_browser_sentinels", 0)) < int(topology["real_browser_sentinels_min"]):
            reasons.add("scale_browser_sentinels_incomplete")
        if set(item.get("scenario_modes") or []) != set(topology["required_modes"]):
            reasons.add("scale_mode_coverage_incomplete")
        measurements = item.get("measurements")
        if not isinstance(measurements, Mapping) or any(
            not _is_number(measurements.get(metric)) for metric in topology["tier_required_metrics"]
        ):
            reasons.add("scale_tier_measurements_incomplete")

    calibration = result.get("calibration")
    if (
        not isinstance(calibration, Mapping)
        or set(calibration) != {"status", "max_relative_error"}
        or calibration.get("status") != "completed"
        or not _is_number(calibration.get("max_relative_error"))
        or float(calibration["max_relative_error"]) > float(topology["calibration_max_relative_error"])
    ):
        reasons.add("scale_browser_calibration_failed")
    pairwise = result.get("pairwise_regression")
    if (
        not isinstance(pairwise, Mapping)
        or set(pairwise) != {"status", "publisher_peer_connections", "receiver_peer_connections"}
        or pairwise.get("status") != "completed"
        or pairwise.get("publisher_peer_connections") != 1
        or pairwise.get("receiver_peer_connections") != 1
    ):
        reasons.add("direct_pair_regression_failed")
    return reasons


def evaluate_external_result(
    result: Mapping[str, Any],
    *,
    profile: AdvancedGateProfile,
    expected_digests: Mapping[str, str],
) -> tuple[str, ...]:
    if scan_content_free_document(result):
        return ("external_result_content_boundary_invalid",)
    required = {
        "schema",
        "gate_id",
        "profile_id",
        "status",
        "test_fixture",
        "adapter",
        "environment_digests",
        "execution",
        "scenario_results",
        "measurements",
        "assertions",
        "cleanup",
        "started_at",
        "ended_at",
    }
    if profile.gate_id == "SFB-GATE-008":
        required |= {"tiers", "calibration", "pairwise_regression"}
    if set(result) != required or result.get("schema") != profile.external_result_schema:
        return ("external_result_shape_invalid",)

    reasons: set[str] = set()
    if result.get("gate_id") != profile.gate_id or result.get("profile_id") != profile.profile_id:
        reasons.add("external_result_profile_binding_invalid")
    if result.get("status") != "completed":
        reasons.add("external_execution_incomplete")
    if result.get("test_fixture") is not False:
        reasons.add("test_fixture_evidence_forbidden")

    adapter = result.get("adapter")
    if not isinstance(adapter, Mapping) or set(adapter) != {
        "contract",
        "real_processes",
        "mocked_components",
        "capabilities",
        "orchestration_owner",
    }:
        reasons.add("external_adapter_attestation_invalid")
    else:
        if (
            adapter.get("contract") != ADAPTER_CONTRACT
            or adapter.get("real_processes") is not True
            or adapter.get("mocked_components") != []
            or adapter.get("orchestration_owner") != "hub_external_harness"
        ):
            reasons.add("external_adapter_attestation_invalid")
        if not isinstance(adapter.get("capabilities"), list) or not set(
            profile.document["required_capabilities"]
        ).issubset(set(adapter.get("capabilities") or [])):
            reasons.add("external_adapter_capabilities_incomplete")

    digests = result.get("environment_digests")
    if not isinstance(digests, Mapping) or set(digests) != SHA256_NAMES:
        reasons.add("external_environment_digests_invalid")
    elif any(not _is_hex64(value) for value in digests.values()) or dict(digests) != dict(expected_digests):
        reasons.add("external_environment_digest_mismatch")

    execution = result.get("execution")
    expected_execution = {
        "seed": profile.document["seed"],
        "warmup_seconds": profile.execution["warmup_seconds"],
        "measurement_seconds": profile.execution["measurement_seconds"],
        "repetitions": profile.execution["repetitions"],
    }
    if not isinstance(execution, Mapping) or dict(execution) != expected_execution:
        reasons.add("external_execution_binding_invalid")

    started_at = _utc_timestamp(result.get("started_at"))
    ended_at = _utc_timestamp(result.get("ended_at"))
    if started_at is None or ended_at is None or ended_at <= started_at:
        reasons.add("external_execution_window_invalid")

    scenario_results = result.get("scenario_results")
    if not isinstance(scenario_results, list):
        reasons.add("external_scenario_coverage_invalid")
    else:
        observed: list[str] = []
        for item in scenario_results:
            if not isinstance(item, Mapping) or set(item) != {"scenario_id", "status"}:
                reasons.add("external_scenario_shape_invalid")
                continue
            observed.append(str(item.get("scenario_id")))
            if item.get("status") != "completed":
                reasons.add("external_scenario_failed")
        if observed != list(profile.document["required_scenarios"]) or len(set(observed)) != len(observed):
            reasons.add("external_scenario_coverage_invalid")

    measurements = result.get("measurements")
    if not isinstance(measurements, Mapping) or any(
        not _is_number(measurements.get(metric)) for metric in profile.document["required_metrics"]
    ):
        reasons.add("required_measurements_incomplete")
    else:
        reasons.update(_threshold_reasons(profile, measurements))

    assertions = result.get("assertions")
    if not isinstance(assertions, Mapping) or set(assertions) != set(profile.document["required_assertions"]):
        reasons.add("required_assertions_incomplete")
    elif any(assertions[name] is not True for name in profile.document["required_assertions"]):
        reasons.add("required_assertion_failed")

    cleanup = result.get("cleanup")
    if not isinstance(cleanup, Mapping) or dict(cleanup) != dict(profile.document["cleanup_requirements"]):
        reasons.add("external_cleanup_incomplete")

    if profile.gate_id == "SFB-GATE-008":
        reasons.update(_scale_reasons(profile, result))
    return tuple(sorted(reasons))


def _normalized_measurements(profile: AdvancedGateProfile, result: Mapping[str, Any] | None) -> dict[str, Any]:
    if result is None or not isinstance(result.get("measurements"), Mapping):
        return {}
    normalized = {
        metric: result["measurements"].get(metric)
        for metric in profile.document["required_metrics"]
        if _is_number(result["measurements"].get(metric))
    }
    if profile.gate_id == "SFB-GATE-008" and isinstance(result.get("tiers"), list):
        normalized["tiers"] = [
            {
                "receiver_count": item.get("receiver_count"),
                "status": item.get("status"),
                "real_browser_sentinels": item.get("real_browser_sentinels"),
            }
            for item in result["tiers"]
            if isinstance(item, Mapping)
        ]
    return normalized


def run_advanced_gate(
    *,
    profile_path: Path,
    output: Path,
    result_adapter: ExternalGateResultPort | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    profile = load_advanced_gate_profile(profile_path)
    digests = environment_digests(profile, root=root)
    external_result: Mapping[str, Any] | None = None
    if result_adapter is None:
        reasons = ("external_result_missing",)
        status = "blocked"
    else:
        external_result = result_adapter.read(maximum_bytes=profile.execution["artifact_bytes_max"])
        reasons = evaluate_external_result(external_result, profile=profile, expected_digests=digests)
        status = "passed" if not reasons else "failed"
    safe_started_at = external_result.get("started_at") if external_result and _utc_timestamp(external_result.get("started_at")) else None
    safe_ended_at = external_result.get("ended_at") if external_result and _utc_timestamp(external_result.get("ended_at")) else None
    report = {
        "schema": profile.artifact_schema,
        "gate_id": profile.gate_id,
        "profile_id": profile.profile_id,
        "status": status,
        "release_blocking": status != "passed",
        "reason_codes": list(reasons),
        "execution_mode": "external_result_validation",
        "claims": {
            "real_processes_verified": status == "passed",
            "browser_evidence_verified": status == "passed" and "real_browser_processes" in profile.document["required_capabilities"],
            "load_evidence_verified": status == "passed" and profile.gate_id in {"SFB-GATE-005", "SFB-GATE-008"},
            "chaos_evidence_verified": status == "passed" and profile.gate_id in {"SFB-GATE-006", "SFB-GATE-007"},
        },
        "environment_digests": digests,
        "external_result_sha256": canonical_sha256(external_result) if external_result is not None else None,
        "started_at": safe_started_at,
        "ended_at": safe_ended_at,
        "measurements": _normalized_measurements(profile, external_result),
        "plan": build_plan(profile, digests=digests),
    }
    atomic_write_report(output, report)
    return report


def gate_cli(*, default_profile: Path, default_output: Path) -> int:
    parser = argparse.ArgumentParser(description="Validate external real-system SFU broadcast gate evidence.")
    parser.add_argument("--profile", type=Path, default=default_profile)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--output", type=Path, default=default_output)
    args = parser.parse_args()
    try:
        adapter = FileExternalGateResultAdapter(args.external_result) if args.external_result else None
        report = run_advanced_gate(profile_path=args.profile, output=args.output, result_adapter=adapter)
    except SfuBroadcastGateError as exc:
        print(json.dumps({"status": "failed", "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


__all__ = [
    "ADAPTER_CONTRACT",
    "AdvancedGateProfile",
    "ExternalGateResultPort",
    "FileExternalGateResultAdapter",
    "build_plan",
    "environment_digests",
    "evaluate_external_result",
    "gate_cli",
    "load_advanced_gate_profile",
    "run_advanced_gate",
]
