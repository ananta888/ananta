from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _domain_inventory_exists(path: str) -> bool:
    return (ROOT / path).exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run release gate with integrated E2E dogfood checks.")
    parser.add_argument("--strict", action="store_true", help="Enable strict mode for release_gate.py.")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip E2E dogfood checks.")
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
