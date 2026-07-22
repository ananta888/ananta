#!/usr/bin/env python3
"""Validate real-browser SFU broadcast accessibility and lifecycle evidence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sfu_broadcast_release_common import (  # noqa: E402
    atomic_write_report, build_report, canonical_sha256, content_reasons,
    read_bounded_json, unavailable_report, validate_bindings,
    validate_exceptions,
)

DEFAULT_PROFILE = ROOT / "config/test-profiles/sfu-broadcast/accessibility.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-accessibility.json"
GATE_ID = "SFB-GATE-014"
REPORT_SCHEMA = "ananta.sfu-broadcast-accessibility-gate.v1"


def evaluate(
    profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    as_of: date,
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: set[str] = set(content_reasons(evidence))
    binding_reasons, bindings = validate_bindings(
        evidence,
        expected_config_sha256=canonical_sha256(profile),
        required_image_ids=profile.get("required_image_ids", []),
    )
    reasons.update(binding_reasons)
    if evidence.get("schema") != "ananta.sfu-broadcast-real-accessibility-result.v1":
        reasons.add("accessibility_schema_invalid")
    if evidence.get("status") != "passed":
        reasons.add("accessibility_external_status_not_passed")
    execution = evidence.get("execution")
    if not isinstance(execution, Mapping):
        reasons.add("accessibility_execution_missing")
        execution = {}
    if execution.get("mock_used") is not False or execution.get("real_media") is not True:
        reasons.add("accessibility_real_media_missing")
    processes = execution.get("browser_processes")
    if not isinstance(processes, list):
        reasons.add("accessibility_browser_process_inventory_invalid")
        processes = []
    observed_browsers = {
        row.get("browser")
        for row in processes
        if isinstance(row, Mapping)
        and row.get("real_process") is True
        and row.get("viewport_emulation_only") is False
    }
    if observed_browsers != set(profile.get("required_browsers", [])):
        reasons.add("accessibility_browser_matrix_incomplete")
    if len(processes) < int(profile.get("limits", {}).get("browser_processes_min", 0)):
        reasons.add("accessibility_browser_process_count_insufficient")

    checks = evidence.get("checks")
    if not isinstance(checks, Mapping) or any(
        checks.get(name) is not True for name in profile.get("required_checks", [])
    ):
        reasons.add("accessibility_keyboard_semantics_failed")
    responsive = evidence.get("responsive")
    if not isinstance(responsive, Mapping) or any(
        responsive.get(name) is not True for name in profile.get("responsive_checks", [])
    ):
        reasons.add("accessibility_responsive_checks_failed")

    axe = evidence.get("axe")
    if not isinstance(axe, Mapping):
        reasons.add("accessibility_axe_result_missing")
        axe = {}
    if axe.get("package") != profile.get("axe", {}).get("package"):
        reasons.add("accessibility_axe_adapter_mismatch")
    if axe.get("violations") != 0:
        reasons.add("accessibility_axe_violation")
    reasons.update(validate_exceptions(
        axe.get("exceptions"),
        as_of=as_of,
        allowed_scopes=set(profile.get("required_browsers", [])),
    ))

    lifecycle = evidence.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        reasons.add("accessibility_lifecycle_missing")
        lifecycle = {}
    started = lifecycle.get("started")
    if not isinstance(started, Mapping) or any(
        not isinstance(started.get(name), int) or started.get(name, 0) < 1
        for name in profile.get("required_started_components", [])
    ):
        reasons.add("accessibility_productive_lifecycle_not_started")
    cleanup = lifecycle.get("cleanup")
    if not isinstance(cleanup, Mapping):
        reasons.add("accessibility_cleanup_inventory_missing")
        cleanup = {}
    for scenario in profile.get("cleanup_scenarios", []):
        row = cleanup.get(scenario)
        if not isinstance(row, Mapping) or any(
            row.get(name) != 0 for name in profile.get("required_cleanup_components", [])
        ):
            reasons.add(f"accessibility_cleanup_{scenario}_incomplete")

    screen_reader = evidence.get("screen_reader")
    claimed = isinstance(screen_reader, Mapping) and screen_reader.get(
        "compatibility_claimed"
    ) is True
    if claimed:
        accepted = set(profile.get("screen_reader", {}).get("accepted_automation", []))
        if (
            screen_reader.get("automation") not in accepted
            or screen_reader.get("real_device_or_desktop") is not True
            or screen_reader.get("evidence_verified") is not True
        ):
            reasons.add("accessibility_screen_reader_claim_unverified")
    elif not isinstance(screen_reader, Mapping) or screen_reader.get("status") != "not_claimed":
        reasons.add("accessibility_screen_reader_status_ambiguous")

    peak_memory = evidence.get("resource_peaks", {}).get("memory_bytes") if isinstance(
        evidence.get("resource_peaks"), Mapping
    ) else None
    maximum_memory = profile.get("limits", {}).get("memory_bytes_max")
    if (
        not isinstance(peak_memory, int)
        or not isinstance(maximum_memory, int)
        or peak_memory > maximum_memory
    ):
        reasons.add("accessibility_memory_limit_invalid")

    return build_report(
        schema=REPORT_SCHEMA,
        gate_id=GATE_ID,
        reasons=reasons,
        bindings=bindings,
        summary={
            "real_media": execution.get("real_media"),
            "browser_process_count": len(processes),
            "axe_violations": axe.get("violations"),
            "screen_reader_compatibility_claimed": claimed,
            "cleanup_scenario_count": len(cleanup),
        },
        parent=parent,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--parent-evidence", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    profile = read_bounded_json(args.profile)
    parent = read_bounded_json(args.parent_evidence) if args.parent_evidence else None
    report = (
        evaluate(
            profile, read_bounded_json(args.external_result),
            as_of=args.as_of, parent=parent,
        )
        if args.external_result
        else unavailable_report(
            schema=REPORT_SCHEMA, gate_id=GATE_ID,
            reason="accessibility_external_evidence_missing", config=profile,
            parent=parent,
        )
    )
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
