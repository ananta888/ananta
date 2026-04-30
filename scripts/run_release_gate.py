from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLANNING_UTILS_PATH = ROOT / "agent" / "services" / "planning_utils.py"
TEAMS_ROUTE_PATH = ROOT / "agent" / "routes" / "teams.py"
HARDCODED_TEMPLATE_LITERAL_PATTERN = re.compile(r"(?m)^GOAL_TEMPLATES\s*=\s*\{")
SEED_BLUEPRINT_LITERAL_PATTERN = re.compile(r"(?m)^SEED_BLUEPRINTS\s*=\s*\{")
INITIAL_TASKS_LITERAL_PATTERN = re.compile(r"(?m)^[A-Z0-9_]+_INITIAL_TASKS\s*=\s*\[")
DEFAULT_DOCS_DRIFT_TESTS = (
    "tests/test_cli_docs_contract.py",
    "tests/test_docs_presence.py",
    "tests/test_bootstrap_docs.py",
)


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _domain_inventory_exists(path: str) -> bool:
    return (ROOT / path).exists()


def _resolve_output_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return candidate


def _load_json_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_evidence_refs(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _planning_cleanup_violations(*, planning_utils_text: str, teams_text: str) -> list[str]:
    violations: list[str] = []
    if HARDCODED_TEMPLATE_LITERAL_PATTERN.search(planning_utils_text):
        violations.append("hardcoded_goal_templates_literal_in_planning_utils")
    if SEED_BLUEPRINT_LITERAL_PATTERN.search(teams_text):
        violations.append("seed_blueprints_literal_in_routes_teams")
    if INITIAL_TASKS_LITERAL_PATTERN.search(teams_text):
        violations.append("initial_tasks_literal_in_routes_teams")
    return violations


def _check_planning_cleanup(root: Path = ROOT) -> tuple[bool, str]:
    try:
        planning_utils_text = (root / PLANNING_UTILS_PATH.relative_to(ROOT)).read_text(encoding="utf-8")
        teams_text = (root / TEAMS_ROUTE_PATH.relative_to(ROOT)).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"planning_cleanup_check_file_read_error:{exc}"

    violations = _planning_cleanup_violations(
        planning_utils_text=planning_utils_text,
        teams_text=teams_text,
    )
    if violations:
        return (
            False,
            "planning_cleanup_violation:"
            + ",".join(violations)
            + " (expected catalog path: PlanningTemplateCatalog and SeedBlueprintCatalog)",
        )
    return True, "ok"


def _evaluate_tdd_smoke_report(report_path: str) -> tuple[bool, str, list[str]]:
    path = _resolve_output_path(report_path)
    if not path.exists():
        return False, "tdd_report_missing", []
    try:
        report = _load_json_report(path)
    except (OSError, json.JSONDecodeError):
        return False, "tdd_report_invalid_json", []

    claims = report.get("claims") or {}
    phases = report.get("phases") or {}
    evidence_refs = _normalize_evidence_refs(report.get("evidence_refs"))
    green_status = str(((phases.get("green") or {}).get("status") or "")).strip().lower()
    green_claimed = bool(claims.get("green_phase_claimed"))
    if green_claimed and green_status != "green_passed":
        return False, "green_phase_claimed_without_passing_test_evidence", evidence_refs

    red_status = str(((phases.get("red") or {}).get("status") or "")).strip().lower()
    red_claimed = bool(claims.get("red_phase_claimed"))
    degraded_reason = str(((phases.get("degraded") or {}).get("reason") or "")).strip()
    red_skipped = (not red_claimed) or red_status in {"", "red_missing", "skipped", "not_run"}
    if red_skipped and not degraded_reason:
        return False, "red_phase_skipped_without_degraded_explanation", evidence_refs

    if not evidence_refs:
        return False, "tdd_evidence_refs_missing", evidence_refs
    return True, "ok", evidence_refs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run release gate with integrated E2E dogfood checks.")
    parser.add_argument("--strict", action="store_true", help="Enable strict mode for release_gate.py.")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip E2E dogfood checks.")
    parser.add_argument(
        "--docs-drift-check",
        choices=("off", "report", "strict"),
        default="off",
        help="Run docs drift checks: off (default), report (non-blocking), or strict (blocking).",
    )
    parser.add_argument(
        "--docs-drift-test",
        action="append",
        default=[],
        help="Override/add pytest test target for docs drift checks (repeatable).",
    )
    parser.add_argument(
        "--provider-boundary-check",
        choices=("off", "report", "strict"),
        default="off",
        help="Run core/provider boundary checker: off (default), report (non-blocking), or strict (blocking).",
    )
    parser.add_argument(
        "--provider-boundary-config",
        default="config/core_provider_boundary.json",
        help="Config path for core/provider boundary checker.",
    )
    parser.add_argument(
        "--skip-security-invariants",
        action="store_true",
        help="Skip OSS security invariant checks (not recommended for merge readiness).",
    )
    parser.add_argument("--skip-domain-audit", action="store_true", help="Skip generic domain integration audit.")
    parser.add_argument(
        "--worker-runtime-claimed",
        action="store_true",
        help="Run worker unit/E2E gate checks when native worker runtime is part of release claim.",
    )
    parser.add_argument("--skip-worker-checks", action="store_true", help="Skip native worker gate checks.")
    parser.add_argument(
        "--cli-runtime-claimed",
        action="store_true",
        help="Run unified CLI smoke checks when user-facing CLI runtime is part of release claim.",
    )
    parser.add_argument("--skip-cli-smoke", action="store_true", help="Skip unified CLI smoke checks.")
    parser.add_argument("--cli-smoke-test", default="tests/smoke/test_unified_cli_smoke.py")
    parser.add_argument(
        "--workflow-runtime-claimed",
        action="store_true",
        help="Run workflow adapter smoke checks when workflow automation is part of release claim.",
    )
    parser.add_argument("--skip-workflow-smoke", action="store_true", help="Skip workflow adapter smoke checks.")
    parser.add_argument("--workflow-smoke-test", default="tests/smoke/test_workflow_integration_smoke.py")
    parser.add_argument(
        "--tdd-runtime-claimed",
        action="store_true",
        help="Run TDD tiny-repo smoke checks when TDD blueprint runtime is part of release claim.",
    )
    parser.add_argument("--skip-tdd-smoke", action="store_true", help="Skip TDD tiny-repo smoke checks.")
    parser.add_argument("--tdd-out", default="artifacts/tdd/tdd_blueprint_smoke_report.json")
    parser.add_argument("--domain-inventory", default="data/domain_runtime_inventory.json")
    parser.add_argument("--domain-audit-out", default="artifacts/domain/domain_integration_audit_report.json")
    parser.add_argument("--security-invariant-out", default="artifacts/security/security_invariant_gate_report.json")
    parser.add_argument("--worker-out", default="artifacts/worker/worker_gate_report.json")
    parser.add_argument("--e2e-artifact-root", default="artifacts/e2e")
    parser.add_argument("--e2e-out", default="artifacts/e2e/dogfood_gate_report.json")
    args = parser.parse_args()

    python_exec = _python_executable()
    release_gate_command = [python_exec, "scripts/release_gate.py"]
    if args.strict:
        release_gate_command.append("--strict")

    release_gate_result = subprocess.run(release_gate_command, cwd=str(ROOT), check=False)
    if release_gate_result.returncode != 0:
        return release_gate_result.returncode

    planning_cleanup_ok, planning_cleanup_reason = _check_planning_cleanup()
    if not planning_cleanup_ok:
        print(f"planning_cleanup_error={planning_cleanup_reason}")
        return 1

    if args.provider_boundary_check != "off":
        boundary_command = [
            python_exec,
            "scripts/check_core_provider_boundaries.py",
            "--mode",
            args.provider_boundary_check,
            "--config",
            args.provider_boundary_config,
        ]
        boundary_result = subprocess.run(boundary_command, cwd=str(ROOT), check=False)
        if boundary_result.returncode != 0 and args.provider_boundary_check == "strict":
            return boundary_result.returncode

    if not args.skip_security_invariants:
        security_command = [
            python_exec,
            "scripts/run_security_invariant_checks.py",
            "--out",
            args.security_invariant_out,
        ]
        security_result = subprocess.run(security_command, cwd=str(ROOT), check=False)
        if security_result.returncode != 0:
            return security_result.returncode

    if (not args.skip_domain_audit) and _domain_inventory_exists(args.domain_inventory):
        domain_audit_command = [
            python_exec,
            "scripts/audit_domain_integrations.py",
            "--inventory",
            args.domain_inventory,
            "--out",
            args.domain_audit_out,
        ]
        domain_audit_result = subprocess.run(domain_audit_command, cwd=str(ROOT), check=False)
        if domain_audit_result.returncode != 0:
            return domain_audit_result.returncode

    if args.worker_runtime_claimed and not args.skip_worker_checks:
        worker_command = [
            python_exec,
            "scripts/run_worker_checks.py",
            "--out",
            args.worker_out,
        ]
        if args.strict:
            worker_command.append("--strict")
        worker_result = subprocess.run(worker_command, cwd=str(ROOT), check=False)
        if worker_result.returncode != 0:
            return worker_result.returncode

    if args.cli_runtime_claimed and not args.skip_cli_smoke:
        cli_smoke_command = [
            python_exec,
            "-m",
            "pytest",
            "-q",
            args.cli_smoke_test,
        ]
        cli_smoke_result = subprocess.run(cli_smoke_command, cwd=str(ROOT), check=False)
        if cli_smoke_result.returncode != 0:
            return cli_smoke_result.returncode

    if args.workflow_runtime_claimed and not args.skip_workflow_smoke:
        workflow_smoke_command = [
            python_exec,
            "-m",
            "pytest",
            "-q",
            args.workflow_smoke_test,
        ]
        workflow_smoke_result = subprocess.run(workflow_smoke_command, cwd=str(ROOT), check=False)
        if workflow_smoke_result.returncode != 0:
            return workflow_smoke_result.returncode

    if args.tdd_runtime_claimed and not args.skip_tdd_smoke:
        tdd_smoke_command = [
            python_exec,
            "scripts/run_tdd_blueprint_smoke.py",
            "--out",
            args.tdd_out,
        ]
        if args.strict:
            tdd_smoke_command.append("--strict")
        tdd_smoke_result = subprocess.run(tdd_smoke_command, cwd=str(ROOT), check=False)
        if tdd_smoke_result.returncode != 0:
            return tdd_smoke_result.returncode
        tdd_ok, tdd_reason, tdd_evidence_refs = _evaluate_tdd_smoke_report(args.tdd_out)
        if tdd_evidence_refs:
            print("tdd_evidence_refs=" + ", ".join(tdd_evidence_refs))
        report_path = _resolve_output_path(args.tdd_out)
        report_label = report_path.relative_to(ROOT) if ROOT in report_path.parents else report_path
        print(f"tdd_report={report_label}")
        if not tdd_ok:
            print(f"tdd_gate_error={tdd_reason}")
            return 1

    if args.docs_drift_check != "off":
        docs_drift_command = [
            python_exec,
            "-m",
            "pytest",
            "-q",
            *(args.docs_drift_test or list(DEFAULT_DOCS_DRIFT_TESTS)),
        ]
        docs_drift_result = subprocess.run(docs_drift_command, cwd=str(ROOT), check=False)
        if docs_drift_result.returncode != 0:
            if args.docs_drift_check == "strict":
                return docs_drift_result.returncode
            print("docs_drift_report_error=contract_or_reference_mismatch")

    if args.skip_e2e:
        return 0

    e2e_command = [
        python_exec,
        "scripts/run_e2e_dogfood_checks.py",
        "--artifact-root",
        args.e2e_artifact_root,
        "--out",
        args.e2e_out,
    ]
    e2e_result = subprocess.run(e2e_command, cwd=str(ROOT), check=False)
    return e2e_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
