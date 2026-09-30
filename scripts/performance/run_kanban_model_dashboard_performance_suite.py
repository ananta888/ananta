#!/usr/bin/env python3
"""Aggregate real Kanban performance diagnostics into a formal fail-closed gate."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.performance.kanban_baseline_approval_policy import (
        DEFAULT_POLICY,
        validate_policy_approval,
    )
    from scripts.performance.kanban_performance_environment import (
        collect_commit,
        collect_environment,
    )
    from scripts.performance.kanban_performance_io import (
        load_json,
        write_json_atomic,
    )
    from scripts.performance.kanban_performance_io import (
        sha256_bytes as _sha256_bytes,
    )
    from scripts.performance.kanban_performance_io import (
        source_artifact as _source_artifact,
    )
    from scripts.performance.kanban_performance_measurements import (
        REQUIRED_METRICS,
        normalise_measurements,
    )
    from scripts.performance.kanban_performance_measurements import (
        validate_profile as validate_profile,
    )
    from scripts.performance.kanban_performance_validation import (
        SuiteValidationError,
    )
    from scripts.performance.kanban_performance_validation import (
        mapping as _mapping,
    )
    from scripts.performance.kanban_performance_validation import (
        number as _number,
    )
except ModuleNotFoundError:
    from kanban_baseline_approval_policy import (  # type: ignore
        DEFAULT_POLICY,
        validate_policy_approval,
    )
    from kanban_performance_environment import (  # type: ignore
        collect_commit,
        collect_environment,
    )
    from kanban_performance_io import (  # type: ignore
        load_json,
        write_json_atomic,
    )
    from kanban_performance_io import (
        sha256_bytes as _sha256_bytes,
    )
    from kanban_performance_io import (
        source_artifact as _source_artifact,
    )
    from kanban_performance_measurements import (  # type: ignore
        REQUIRED_METRICS,
        normalise_measurements,
    )
    from kanban_performance_measurements import (
        validate_profile as validate_profile,
    )
    from kanban_performance_validation import (  # type: ignore
        SuiteValidationError,
    )
    from kanban_performance_validation import (
        mapping as _mapping,
    )
    from kanban_performance_validation import (
        number as _number,
    )

ROOT = Path(__file__).resolve().parents[2]
BASELINE_SCHEMA = "ananta.kanban-model-dashboard.performance-baseline.v1"
GATE_SCHEMA = "ananta.kanban-model-dashboard.performance-gate.v1"
DEFAULT_PROFILE = (
    ROOT
    / "config"
    / "test-profiles"
    / "kanban-model-dashboard"
    / "formal-performance.v1.json"
)
DEFAULT_BACKEND = ROOT / "artifacts" / "kanban-local-performance-diagnostic.json"
DEFAULT_ANGULAR = ROOT / "artifacts" / "angular-kanban-local-performance-diagnostic.json"
DEFAULT_TUI = ROOT / "artifacts" / "tui-kanban-local-performance-diagnostic.json"
DEFAULT_PTY = ROOT / "artifacts" / "tui-kanban-pty-resize-local-diagnostic.json"
DEFAULT_CANDIDATE = (
    ROOT
    / "artifacts"
    / "test-gates"
    / "kanban-model-dashboard-performance-baseline-candidate.v1.json"
)
DEFAULT_GATE = (
    ROOT
    / "artifacts"
    / "test-gates"
    / "kanban-model-dashboard-performance-gate.v1.json"
)



def evaluate_absolute(
    measurements: dict[str, float | int],
    profile: dict[str, Any],
) -> dict[str, Any]:
    budgets = _mapping(profile.get("absolute_budgets"), "absolute_budgets")
    checks: dict[str, Any] = {}
    for metric in REQUIRED_METRICS:
        if metric not in measurements:
            raise SuiteValidationError(f"measurement_missing:{metric}")
        actual = _number(measurements[metric], f"measurement:{metric}")
        budget = _mapping(budgets.get(metric), f"budget:{metric}")
        expected = _number(budget.get("value"), f"budget_value:{metric}")
        operator = budget.get("operator")
        passed = (
            actual <= expected
            if operator == "<="
            else actual >= expected
            if operator == ">="
            else math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)
        )
        checks[metric] = {
            "actual": actual,
            "operator": operator,
            "budget": expected,
            "passed": passed,
        }
    return {
        "within_budget": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def load_suite_inputs(
    *,
    profile_path: Path,
    backend_path: Path,
    angular_path: Path,
    tui_path: Path,
    pty_path: Path,
) -> dict[str, Any]:
    profile, profile_bytes = load_json(profile_path)
    backend, backend_bytes = load_json(backend_path)
    angular, angular_bytes = load_json(angular_path)
    tui, tui_bytes = load_json(tui_path)
    pty, pty_bytes = load_json(pty_path)
    measurements, details = normalise_measurements(
        profile=profile,
        backend=backend,
        angular=angular,
        tui=tui,
        pty=pty,
    )
    return {
        "profile": profile,
        "profile_sha256": _sha256_bytes(profile_bytes),
        "measurements": measurements,
        "details": details,
        "sources": {
            "backend": _source_artifact(backend_path, backend_bytes, backend),
            "angular": _source_artifact(angular_path, angular_bytes, angular),
            "tui": _source_artifact(tui_path, tui_bytes, tui),
            "pty": _source_artifact(pty_path, pty_bytes, pty),
        },
    }


def build_baseline_candidate(
    *,
    profile: dict[str, Any],
    profile_sha256: str,
    measurements: dict[str, float | int],
    details: dict[str, Any],
    sources: dict[str, Any],
    environment: dict[str, Any],
    commit: dict[str, str],
    created_at: str | None = None,
) -> dict[str, Any]:
    absolute = evaluate_absolute(measurements, profile)
    if not absolute["within_budget"]:
        raise SuiteValidationError("candidate_absolute_budget_failed")
    return {
        "schema": BASELINE_SCHEMA,
        "baseline_version": 1,
        "profile": {
            "id": profile["profile_id"],
            "schema": profile["schema"],
            "sha256": profile_sha256,
        },
        "approval_status": "candidate_unapproved",
        "approved_by": None,
        "approved_at": None,
        "candidate_created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "environment": environment,
        "measurements": measurements,
        "measurement_details": details,
        "source_artifacts": sources,
        "absolute_evaluation": absolute,
        "candidate_status": "ready_for_policy_evaluation",
    }


def evaluate_baseline(
    *,
    profile: dict[str, Any],
    profile_sha256: str,
    measurements: dict[str, float | int],
    environment: dict[str, Any],
    baseline: dict[str, Any],
    approval_policy: dict[str, Any] | None = None,
    approval_policy_sha256: str | None = None,
) -> dict[str, Any]:
    if baseline.get("schema") != BASELINE_SCHEMA or baseline.get("baseline_version") != 1:
        raise SuiteValidationError("baseline_schema_invalid")
    baseline_profile = _mapping(baseline.get("profile"), "baseline_profile")
    profile_compatible = (
        baseline_profile.get("id") == profile.get("profile_id")
        and baseline_profile.get("schema") == profile.get("schema")
        and baseline_profile.get("sha256") == profile_sha256
    )
    baseline_environment = _mapping(
        baseline.get("environment"),
        "baseline_environment",
    )
    environment_compatible = (
        baseline_environment.get("compatibility_sha256")
        == environment.get("compatibility_sha256")
        and baseline_environment.get("compatibility")
        == environment.get("compatibility")
    )
    approval_status = str(baseline.get("approval_status") or "")
    approval_valid = bool(
        approval_status == "approved"
        and approval_policy is not None
        and isinstance(approval_policy_sha256, str)
        and validate_policy_approval(
            baseline=baseline,
            policy=approval_policy,
            policy_sha256=approval_policy_sha256,
        )
    )
    prior = _mapping(baseline.get("measurements"), "baseline_measurements")
    comparison = _mapping(profile["baseline_comparison"], "baseline_comparison")
    max_regression = _number(
        comparison.get("max_regression_percent"),
        "max_regression_percent",
    )
    upper_factor = 1.0 + max_regression / 100.0
    lower_factor = 1.0 - max_regression / 100.0
    checks: dict[str, Any] = {}
    for metric, direction in _mapping(
        comparison.get("metrics"),
        "baseline_metric_directions",
    ).items():
        current = _number(measurements.get(metric), f"current:{metric}")
        previous = _number(prior.get(metric), f"baseline:{metric}")
        if direction == "lower_is_better":
            limit = previous * upper_factor
            passed = current <= limit + 1e-12
        elif direction == "higher_is_better":
            limit = previous * lower_factor
            passed = current + 1e-12 >= limit
        else:
            limit = previous
            passed = math.isclose(current, previous, rel_tol=0.0, abs_tol=1e-12)
        checks[metric] = {
            "current": current,
            "baseline": previous,
            "direction": direction,
            "limit": limit,
            "passed": passed,
        }
    return {
        "baseline_schema": baseline["schema"],
        "approval_status": approval_status,
        "required_approval_status": comparison["required_approval_status"],
        "approval_valid": approval_valid,
        "profile_compatible": profile_compatible,
        "environment_compatible": environment_compatible,
        "comparison_computed": True,
        "formal_comparison_eligible": approval_valid,
        "max_regression_percent": max_regression,
        "within_regression_limit": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def build_gate_report(
    *,
    profile: dict[str, Any],
    profile_sha256: str,
    measurements: dict[str, float | int],
    details: dict[str, Any],
    sources: dict[str, Any],
    environment: dict[str, Any],
    commit: dict[str, str],
    baseline: dict[str, Any],
    approval_policy: dict[str, Any] | None = None,
    approval_policy_sha256: str | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    absolute = evaluate_absolute(measurements, profile)
    baseline_evaluation = evaluate_baseline(
        profile=profile,
        profile_sha256=profile_sha256,
        measurements=measurements,
        environment=environment,
        baseline=baseline,
        approval_policy=approval_policy,
        approval_policy_sha256=approval_policy_sha256,
    )
    blockers: list[dict[str, Any]] = []
    failed_budgets = [
        metric
        for metric, check in absolute["checks"].items()
        if not check["passed"]
    ]
    if failed_budgets:
        blockers.append(
            {"code": "absolute_budget_exceeded", "metrics": failed_budgets}
        )
    if not baseline_evaluation["profile_compatible"]:
        blockers.append({"code": "baseline_profile_mismatch"})
    if not baseline_evaluation["environment_compatible"]:
        blockers.append({"code": "baseline_environment_mismatch"})
    approval_status = baseline_evaluation["approval_status"]
    if approval_status == "candidate_unapproved":
        blockers.append({"code": "baseline_approval_required"})
    elif not baseline_evaluation["approval_valid"]:
        blockers.append({"code": "baseline_approval_invalid"})
    if not baseline_evaluation["within_regression_limit"]:
        failed_regressions = [
            metric
            for metric, check in baseline_evaluation["checks"].items()
            if not check["passed"]
        ]
        blockers.append(
            {
                "code": "baseline_regression_exceeded",
                "metrics": failed_regressions,
            }
        )
    passed = not blockers
    approval_only = (
        len(blockers) == 1
        and blockers[0]["code"] == "baseline_approval_required"
    )
    return {
        "schema": GATE_SCHEMA,
        "suite_id": "kanban-model-dashboard.performance.v1",
        "scope": "formal_performance_gate",
        "status": "passed" if passed else "blocked" if approval_only else "failed",
        "release_evidence": passed,
        "formal_gate_eligible": passed,
        "evidence_classification": (
            "formal_release_evidence"
            if passed
            else "formal_gate_result_not_release_evidence"
        ),
        "evaluated_at": evaluated_at or datetime.now(timezone.utc).isoformat(),
        "profile": {
            "id": profile["profile_id"],
            "schema": profile["schema"],
            "sha256": profile_sha256,
        },
        "commit": commit,
        "environment": environment,
        "measurements": measurements,
        "measurement_details": details,
        "source_artifacts": sources,
        "absolute_evaluation": absolute,
        "baseline_evaluation": baseline_evaluation,
        "blockers": blockers,
    }


def _add_common_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--backend-result", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--angular-result", type=Path, default=DEFAULT_ANGULAR)
    parser.add_argument("--tui-result", type=Path, default=DEFAULT_TUI)
    parser.add_argument("--pty-result", type=Path, default=DEFAULT_PTY)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    candidate = subparsers.add_parser(
        "candidate",
        help="Create an unapproved candidate from real diagnostics.",
    )
    _add_common_inputs(candidate)
    candidate.add_argument("--output", type=Path, default=DEFAULT_CANDIDATE)
    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate diagnostics against a versioned baseline.",
    )
    _add_common_inputs(evaluate)
    evaluate.add_argument("--baseline", type=Path, required=True)
    evaluate.add_argument("--approval-policy", type=Path, default=DEFAULT_POLICY)
    evaluate.add_argument("--output", type=Path, default=DEFAULT_GATE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        inputs = load_suite_inputs(
            profile_path=args.profile,
            backend_path=args.backend_result,
            angular_path=args.angular_result,
            tui_path=args.tui_result,
            pty_path=args.pty_result,
        )
        environment = collect_environment(
            _mapping(
                inputs["details"].get("angular_runtime"),
                "angular_runtime",
            )
        )
        commit = collect_commit()
        if args.command == "candidate":
            report = build_baseline_candidate(
                profile=inputs["profile"],
                profile_sha256=inputs["profile_sha256"],
                measurements=inputs["measurements"],
                details=inputs["details"],
                sources=inputs["sources"],
                environment=environment,
                commit=commit,
            )
            exit_code = 0
            status = report["candidate_status"]
        else:
            baseline, _baseline_bytes = load_json(args.baseline)
            approval_policy, approval_policy_bytes = load_json(args.approval_policy)
            report = build_gate_report(
                profile=inputs["profile"],
                profile_sha256=inputs["profile_sha256"],
                measurements=inputs["measurements"],
                details=inputs["details"],
                sources=inputs["sources"],
                environment=environment,
                commit=commit,
                baseline=baseline,
                approval_policy=approval_policy,
                approval_policy_sha256=_sha256_bytes(approval_policy_bytes),
            )
            status = report["status"]
            exit_code = 0 if status == "passed" else 2 if status == "blocked" else 1
        write_json_atomic(args.output, report)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "status": status,
                    "blockers": report.get("blockers", []),
                },
                sort_keys=True,
            )
        )
        return exit_code
    except (OSError, SuiteValidationError, subprocess.SubprocessError) as exc:
        print(f"performance_suite_failed:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
