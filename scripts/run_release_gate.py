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
    parser.add_argument("--skip-domain-audit", action="store_true", help="Skip generic domain integration audit.")
    parser.add_argument("--domain-inventory", default="data/domain_runtime_inventory.json")
    parser.add_argument("--domain-audit-out", default="artifacts/domain/domain_integration_audit_report.json")
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
