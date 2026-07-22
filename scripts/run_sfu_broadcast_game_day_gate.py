#!/usr/bin/env python3
"""Validate operator-approved, atomic SFU broadcast game-day evidence."""

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
    parent_activation_reasons, read_bounded_json, unavailable_report,
    validate_bindings,
)

DEFAULT_PROFILE = ROOT / "config/test-profiles/sfu-broadcast/game-day.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-game-day.json"
GATE_ID = "SFB-GATE-GAME-DAY"
REPORT_SCHEMA = "ananta.sfu-broadcast-game-day-gate.v1"


def evaluate(
    profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    parent: Mapping[str, Any] | None,
    operator_approved: bool,
) -> dict[str, Any]:
    reasons: set[str] = set(content_reasons(evidence))
    if not operator_approved:
        reasons.add("game_day_operator_approval_missing")
    reasons.update(parent_activation_reasons(parent))
    binding_reasons, bindings = validate_bindings(
        evidence,
        expected_config_sha256=canonical_sha256(profile),
        required_image_ids=profile.get("required_image_ids", []),
    )
    reasons.update(binding_reasons)
    if evidence.get("schema") != "ananta.sfu-broadcast-real-game-day-result.v1":
        reasons.add("game_day_schema_invalid")
    if evidence.get("status") != "passed":
        reasons.add("game_day_external_status_not_passed")
    if evidence.get("real_execution") is not True or evidence.get("mock_used") is not False:
        reasons.add("game_day_real_execution_missing")
    scenarios = evidence.get("scenarios")
    if not isinstance(scenarios, list):
        reasons.add("game_day_scenario_inventory_invalid")
        scenarios = []
    rows = {row.get("scenario"): row for row in scenarios if isinstance(row, Mapping)}
    if set(rows) != set(profile.get("scenarios", [])):
        reasons.add("game_day_scenario_scope_mismatch")
    rollback_invariants = profile.get("rollback_invariants", [])
    cleanup_invariants = profile.get("cleanup_invariants", [])
    maximum = profile.get("limits", {}).get("rollback_commit_milliseconds_max")
    for row in rows.values():
        if row.get("status") != "passed":
            reasons.add("game_day_scenario_failed")
        rollback = row.get("rollback")
        if not isinstance(rollback, Mapping) or any(
            rollback.get(name) is not True for name in rollback_invariants
        ):
            reasons.add("game_day_rollback_not_atomic")
        duration = rollback.get("commit_milliseconds") if isinstance(rollback, Mapping) else None
        if not isinstance(duration, int) or not isinstance(maximum, int) or duration > maximum:
            reasons.add("game_day_rollback_deadline_exceeded")
        cleanup = row.get("cleanup")
        if not isinstance(cleanup, Mapping) or any(
            cleanup.get(name) is not True for name in cleanup_invariants
        ):
            reasons.add("game_day_cleanup_incomplete")
        if row.get("activation_attempted_under_parent_block") is True:
            reasons.add("game_day_parent_block_bypassed")
    return build_report(
        schema=REPORT_SCHEMA,
        gate_id=GATE_ID,
        reasons=reasons,
        bindings=bindings,
        summary={
            "operator_approved": operator_approved,
            "real_execution": evidence.get("real_execution"),
            "scenario_count": len(rows),
            "atomic_rollback_count": sum(
                isinstance(row.get("rollback"), Mapping)
                and row["rollback"].get("atomic_transition") is True
                for row in rows.values()
            ),
            "all_advanced_flags_disabled": bool(rows) and all(
                isinstance(row.get("rollback"), Mapping)
                and row["rollback"].get("all_advanced_flags_disabled") is True
                for row in rows.values()
            ),
        },
        parent=parent,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--external-result", type=Path)
    parser.add_argument("--parent-evidence", type=Path)
    parser.add_argument("--operator-approved", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    profile = read_bounded_json(args.profile)
    parent = read_bounded_json(args.parent_evidence) if args.parent_evidence else None
    report = (
        evaluate(
            profile, read_bounded_json(args.external_result),
            parent=parent, operator_approved=args.operator_approved,
        )
        if args.external_result
        else unavailable_report(
            schema=REPORT_SCHEMA, gate_id=GATE_ID,
            reason="game_day_external_evidence_missing", config=profile, parent=parent,
        )
    )
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" and report["activation_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
