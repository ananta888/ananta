#!/usr/bin/env python3
"""Validate bounded real-SFU media-plane fuzz evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_release_common import (  # noqa: E402
    atomic_write_report, build_report, canonical_sha256, content_reasons,
    is_sha256, read_bounded_json, unavailable_report, validate_bindings,
)

DEFAULT_PROFILE = ROOT / "config/test-profiles/sfu-broadcast/media-plane-fuzz.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-media-plane-fuzz.json"
GATE_ID = "SFB-GATE-013"
REPORT_SCHEMA = "ananta.sfu-broadcast-media-plane-fuzz-gate.v1"


def evaluate(
    profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: set[str] = set(content_reasons(evidence))
    required_images = profile.get("required_image_ids", [])
    binding_reasons, bindings = validate_bindings(
        evidence,
        expected_config_sha256=canonical_sha256(profile),
        required_image_ids=required_images if isinstance(required_images, list) else (),
    )
    reasons.update(binding_reasons)
    if evidence.get("schema") != "ananta.sfu-broadcast-real-media-fuzz-result.v1":
        reasons.add("media_fuzz_schema_invalid")
    if evidence.get("status") != "passed":
        reasons.add("media_fuzz_external_status_not_passed")
    execution = evidence.get("execution")
    if not isinstance(execution, Mapping):
        reasons.add("media_fuzz_execution_missing")
        execution = {}
    if execution.get("backend") != "real_container" or execution.get("mock_used") is not False:
        reasons.add("media_fuzz_real_backend_missing")
    if execution.get("public_listener_reached") is not True:
        reasons.add("media_fuzz_public_listener_unreached")
    configured_seeds = profile.get("seeds")
    observed_seeds = evidence.get("seeds")
    if (
        not isinstance(configured_seeds, list)
        or not isinstance(observed_seeds, list)
        or sorted(observed_seeds) != sorted(configured_seeds)
        or len(observed_seeds) < int(profile.get("seeds_min", 0))
    ):
        reasons.add("media_fuzz_seed_coverage_invalid")
    if int(evidence.get("cases_per_seed", -1)) < int(profile.get("cases_per_seed_min", 0)):
        reasons.add("media_fuzz_case_count_insufficient")
    if not is_sha256(evidence.get("corpus_sha256")):
        reasons.add("media_fuzz_corpus_digest_invalid")
    if not is_sha256(evidence.get("coverage_sha256")):
        reasons.add("media_fuzz_coverage_digest_invalid")
    required_protocols = set(profile.get("required_protocol_classes", []))
    observed_protocols = set(evidence.get("protocol_classes", [])) if isinstance(
        evidence.get("protocol_classes"), list
    ) else set()
    if observed_protocols != required_protocols:
        reasons.add("media_fuzz_protocol_coverage_mismatch")
    unsupported = set(evidence.get("unsupported_protocol_classes", [])) if isinstance(
        evidence.get("unsupported_protocol_classes"), list
    ) else set()
    if unsupported != set(profile.get("unsupported_internal_protocol_classes", [])):
        reasons.add("media_fuzz_unsupported_inventory_mismatch")
    cross_layer = evidence.get("cross_layer_mutation")
    if not isinstance(cross_layer, Mapping):
        reasons.add("media_fuzz_cross_layer_contract_missing")
    else:
        claimed = int(cross_layer.get("cases", 0)) > 0
        reached = cross_layer.get("public_injection_path_verified") is True
        path = set(cross_layer.get("path_components", [])) if isinstance(
            cross_layer.get("path_components"), list
        ) else set()
        required_path = set(profile.get("cross_layer_mutation", {}).get("required_path_components", []))
        if claimed and (not reached or path != required_path):
            reasons.add("media_fuzz_cross_layer_injection_unverified")
    limits = profile.get("limits", {})
    peaks = evidence.get("resource_peaks")
    if not isinstance(peaks, Mapping):
        reasons.add("media_fuzz_resource_peaks_missing")
        peaks = {}
    for name in (
        "packet_count", "bytes", "cpu_seconds", "memory_bytes",
        "file_descriptors", "sockets", "timeout_seconds",
    ):
        maximum = limits.get(f"{name}_max", limits.get(name))
        value = peaks.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            reasons.add(f"media_fuzz_resource_{name}_invalid")
        elif not isinstance(maximum, (int, float)) or value > maximum:
            reasons.add(f"media_fuzz_resource_{name}_exceeded")
    safety = evidence.get("safety")
    required_safety = (
        "no_crashloop", "no_oom", "cpu_bounded", "allocation_bounded",
        "no_cross_scope_route", "no_auth_e2ee_downgrade", "no_payload_export",
        "hub_signature_boundary_preserved",
    )
    if not isinstance(safety, Mapping) or any(safety.get(name) is not True for name in required_safety):
        reasons.add("media_fuzz_safety_invariant_failed")
    minimized = evidence.get("minimized_failures")
    if not isinstance(minimized, list):
        reasons.add("media_fuzz_minimized_failure_inventory_invalid")
        minimized = []
    for item in minimized:
        if not isinstance(item, Mapping) or set(item) != {
            "seed", "case_sha256", "protocol_class", "reason_code",
        }:
            reasons.add("media_fuzz_minimized_failure_contract_invalid")
            break
        if not is_sha256(item.get("case_sha256")):
            reasons.add("media_fuzz_minimized_failure_digest_invalid")
    return build_report(
        schema=REPORT_SCHEMA,
        gate_id=GATE_ID,
        reasons=reasons,
        bindings=bindings,
        summary={
            "real_backend": execution.get("backend") == "real_container",
            "mock_used": execution.get("mock_used"),
            "seed_count": len(observed_seeds) if isinstance(observed_seeds, list) else 0,
            "protocol_class_count": len(observed_protocols),
            "unsupported_class_count": len(unsupported),
            "minimized_failure_count": len(minimized),
        },
        parent=parent,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--parent-evidence", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    profile = read_bounded_json(args.profile)
    parent = read_bounded_json(args.parent_evidence) if args.parent_evidence else None
    report = (
        evaluate(profile, read_bounded_json(args.external_result), parent=parent)
        if args.external_result
        else unavailable_report(
            schema=REPORT_SCHEMA, gate_id=GATE_ID,
            reason="media_fuzz_external_evidence_missing", config=profile, parent=parent,
        )
    )
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
