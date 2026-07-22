#!/usr/bin/env python3
"""Validate real scale, soak, or chaos evidence without simulating a run."""

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
    read_bounded_json, unavailable_report, validate_bindings,
)

DEFAULT_PROFILE = ROOT / "config/test-profiles/sfu-broadcast/nightly-runtime.v1.json"


def evaluate(
    profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    kind: str,
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: set[str] = set(content_reasons(evidence))
    kind_policy = profile.get("kinds", {}).get(kind)
    if not isinstance(kind_policy, Mapping):
        reasons.add("nightly_gate_kind_invalid")
        kind_policy = {}
    binding_reasons, bindings = validate_bindings(
        evidence,
        expected_config_sha256=canonical_sha256(profile),
        required_image_ids=profile.get("required_image_ids", []),
    )
    reasons.update(binding_reasons)
    if evidence.get("schema") != "ananta.sfu-broadcast-real-nightly-result.v1":
        reasons.add("nightly_gate_schema_invalid")
    if evidence.get("kind") != kind or evidence.get("status") != "passed":
        reasons.add("nightly_gate_external_status_invalid")
    if evidence.get("real_execution") is not True or evidence.get("mock_used") is not False:
        reasons.add("nightly_gate_real_execution_missing")
    metrics = evidence.get("metrics")
    if not isinstance(metrics, Mapping):
        reasons.add("nightly_gate_metrics_missing")
        metrics = {}
    for key in ("receiver_processes_min", "receivers_min", "duration_seconds_min"):
        if key not in kind_policy:
            continue
        metric = key.removesuffix("_min")
        value = metrics.get(metric)
        if not isinstance(value, (int, float)) or value < kind_policy[key]:
            reasons.add(f"nightly_gate_{metric}_insufficient")
    if kind == "chaos":
        observed = set(evidence.get("faults", [])) if isinstance(evidence.get("faults"), list) else set()
        if observed != set(kind_policy.get("required_faults", [])):
            reasons.add("nightly_gate_fault_coverage_mismatch")
    safety = evidence.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(name) is not True for name in profile.get("required_safety", [])
    ):
        reasons.add("nightly_gate_safety_invariant_failed")
    resources = evidence.get("resource_peaks")
    if not isinstance(resources, Mapping):
        reasons.add("nightly_gate_resource_peaks_missing")
        resources = {}
    if (
        not isinstance(resources.get("memory_bytes"), int)
        or resources.get("memory_bytes", 0) > profile.get("limits", {}).get("memory_bytes_max", 0)
    ):
        reasons.add("nightly_gate_memory_limit_invalid")
    if (
        not isinstance(resources.get("elapsed_seconds"), int)
        or resources.get("elapsed_seconds", 0) > profile.get("limits", {}).get("timeout_seconds_max", 0)
    ):
        reasons.add("nightly_gate_timeout_limit_invalid")
    return build_report(
        schema=f"ananta.sfu-broadcast-{kind}-gate.v1",
        gate_id=f"SFB-GATE-{kind.upper()}",
        reasons=reasons,
        bindings=bindings,
        summary={
            "kind": kind,
            "real_execution": evidence.get("real_execution"),
            "receiver_processes": metrics.get("receiver_processes"),
            "receivers": metrics.get("receivers"),
            "duration_seconds": metrics.get("duration_seconds"),
        },
        parent=parent,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--kind", choices=("scale", "soak", "chaos"), required=True)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--parent-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = read_bounded_json(args.profile)
    parent = read_bounded_json(args.parent_evidence) if args.parent_evidence else None
    report = (
        evaluate(
            profile, read_bounded_json(args.external_result),
            kind=args.kind, parent=parent,
        )
        if args.external_result
        else unavailable_report(
            schema=f"ananta.sfu-broadcast-{args.kind}-gate.v1",
            gate_id=f"SFB-GATE-{args.kind.upper()}",
            reason="nightly_gate_external_evidence_missing",
            config=profile,
            parent=parent,
        )
    )
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
