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
            "audit-runtime-claims",
            [
                python_exec,
                "scripts/audit_client_surface_entrypoints.py",
                "--todo",
                "todo.json",
                "--fail-on-warning",
            ],
        ),
        (
            "client-runtime-pytest",
            [
                python_exec,
                "-m",
                "pytest",
                "-q",
                "tests/test_client_surface_api_contracts.py",
                "tests/test_client_surface_security_contracts.py",
                "tests/test_client_surface_runtime_inventory_evidence.py",
                "tests/test_tui_runtime_app.py",
                "tests/test_nvim_runtime_surface.py",
                "tests/test_nvim_runtime_bridge_contract.py",
                "tests/test_eclipse_runtime_bootstrap.py",
                "tests/test_client_surface_golden_path_smoke.py",
                "tests/test_client_surface_core_regressions.py",
                "tests/test_client_surface_merge_readiness_report.py",
                "tests/test_client_surface_post_test_cleanup_todo.py",
                "tests/test_release_gate_contract.py",
            ],
        ),
        ("smoke-tui-runtime", [python_exec, "scripts/smoke_tui_runtime.py"]),
        ("smoke-nvim-runtime", [python_exec, "scripts/smoke_nvim_runtime.py"]),
        ("smoke-eclipse-bootstrap", [python_exec, "scripts/smoke_eclipse_runtime_bootstrap.py"]),
        ("smoke-client-golden-paths", [python_exec, "scripts/smoke_client_golden_paths.py"]),
    )


def _advisory_checks(python_exec: str) -> tuple[tuple[str, list[str]], ...]:
    return (("eclipse-headless-smoke", [python_exec, "scripts/smoke_eclipse_runtime_headless.py"]),)


def _run_check(name: str, command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=os.environ.copy(),
    )
    combined_output = (result.stdout + "\n" + result.stderr).strip()
    return {
        "name": name,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "command": " ".join(command),
        "output_tail": combined_output[-2000:],
    }


def run_gate(include_advisory: bool = False) -> dict[str, Any]:
    python_exec = _python_executable()
    blocking_results = [_run_check(name, command) for name, command in _blocking_checks(python_exec)]
    advisory_results: list[dict[str, Any]] = []
    if include_advisory:
        advisory_results = [_run_check(name, command) for name, command in _advisory_checks(python_exec)]

    blocking_ok = all(item["ok"] for item in blocking_results)
    advisory_ok = all(item["ok"] for item in advisory_results) if advisory_results else True
    return {
        "schema": "client_surface_test_gate_v1",
        "blocking": {
            "ok": blocking_ok,
            "checks": blocking_results,
        },
        "advisory": {
            "executed": include_advisory,
            "ok": advisory_ok,
            "checks": advisory_results,
            "notes": [
                "Advisory checks are non-blocking and may require additional local tooling (for example Docker)."
            ],
        },
        "ok": blocking_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run consolidated client surface runtime test gate.")
    parser.add_argument(
        "--include-advisory",
        action="store_true",
        help="Also run non-blocking advisory checks (for example Eclipse headless smoke).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON report path relative to repository root.",
    )
    args = parser.parse_args()

    report = run_gate(include_advisory=args.include_advisory)
    for bucket_name in ("blocking", "advisory"):
        bucket = report[bucket_name]
        if bucket_name == "advisory" and not bucket["executed"]:
            print("[SKIP] advisory checks not executed (--include-advisory not set)")
            continue
        for check in bucket["checks"]:
            status = "PASS" if check["ok"] else "FAIL"
            print(f"[{status}] {check['name']}: {check['command']}")

    if args.out:
        output_path = Path(args.out)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
