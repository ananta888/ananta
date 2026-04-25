from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _blocking_checks(python_exec: str) -> tuple[tuple[str, list[str]], ...]:
    return (
        (
            "state-ownership",
            [python_exec, "-m", "pytest", "-q", "tests/test_state_ownership_matrix.py"],
        ),
        (
            "policy-and-approval-gatekeeper",
            [
                python_exec,
                "-m",
                "pytest",
                "-q",
                "tests/test_no_policy_bypass.py",
                "tests/test_approval_binding.py",
            ],
        ),
        (
            "trace-and-audit-minimum",
            [
                python_exec,
                "-m",
                "pytest",
                "-q",
                "tests/test_transition_trace_consistency.py",
                "tests/test_audit_append_minimum.py",
            ],
        ),
        (
            "client-thinness",
            [python_exec, "-m", "pytest", "-q", "tests/test_client_thinness.py"],
        ),
        (
            "explicit-context",
            [python_exec, "-m", "pytest", "-q", "tests/test_no_implicit_context.py"],
        ),
        (
            "artifact-provenance",
            [python_exec, "-m", "pytest", "-q", "tests/test_artifact_provenance_hashes.py"],
        ),
    )


def _run_check(name: str, command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=os.environ.copy(),
    )
    combined = (result.stdout + "\n" + result.stderr).strip()
    return {
        "name": name,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "command": " ".join(command),
        "output_tail": combined[-2000:],
    }


def run_gate() -> dict[str, Any]:
    python_exec = _python_executable()
    checks = [_run_check(name, command) for name, command in _blocking_checks(python_exec)]
    blocking_ok = all(item["ok"] for item in checks)
    return {
        "schema": "security_invariant_gate_v1",
        "profile": "oss_core",
        "blocking": {
            "ok": blocking_ok,
            "checks": checks,
        },
        "deferred_hardening": {
            "scope": "kritis_enterprise",
            "ok": True,
            "checks": [],
            "notes": [
                "Excluded from OSS blocking gate: WORM storage, signed ledger, SIEM export, regulated attestation."
            ],
        },
        "ok": blocking_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OSS core security invariant checks.")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON report path relative to repository root.",
    )
    args = parser.parse_args()

    report = run_gate()
    for check in report["blocking"]["checks"]:
        status = "PASS" if check["ok"] else "FAIL"
        print(f"[{status}] {check['name']}: {check['command']}")
    print(
        "[INFO] deferred_hardening: "
        "KRITIS/Enterprise controls are reported separately and are not OSS blocking checks."
    )

    if args.out:
        output_path = Path(args.out)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

