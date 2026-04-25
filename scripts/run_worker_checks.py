from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from worker.core.redaction import enforce_redaction_gate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKER_TEST_TARGETS = [
    "tests/worker",
]
DEFAULT_WORKER_E2E_TARGETS = [
    "tests/e2e/worker/test_tiny_repo_patch_flow.py",
    "tests/e2e/worker/test_failing_test_repair_flow.py",
    "tests/e2e/worker/test_denied_command_flow.py",
    "tests/e2e/worker/test_controlled_loop_flow.py",
]


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(ROOT), check=False, capture_output=True, text=True)


def run_worker_checks(
    *,
    skip_unit: bool = False,
    skip_e2e: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    python_exec = _python_executable()
    checks: list[dict[str, Any]] = []
    evidence_refs: list[str] = []

    if not skip_unit:
        unit_command = [python_exec, "-m", "pytest", "-q", *DEFAULT_WORKER_TEST_TARGETS]
        unit_result = _run_command(unit_command)
        checks.append(
            {
                "name": "worker-unit-tests",
                "ok": unit_result.returncode == 0,
                "returncode": unit_result.returncode,
                "command": " ".join(unit_command),
                "output_tail": (unit_result.stdout + "\n" + unit_result.stderr)[-3000:],
            }
        )
        evidence_refs.append("tests/worker")
        if unit_result.returncode != 0 and strict:
            return {
                "schema": "worker_checks_report.v1",
                "ok": False,
                "checks": checks,
                "evidence_refs": evidence_refs,
            }

    if not skip_e2e:
        e2e_command = [python_exec, "-m", "pytest", "-q", *DEFAULT_WORKER_E2E_TARGETS]
        e2e_result = _run_command(e2e_command)
        checks.append(
            {
                "name": "worker-core-e2e",
                "ok": e2e_result.returncode == 0,
                "returncode": e2e_result.returncode,
                "command": " ".join(e2e_command),
                "output_tail": (e2e_result.stdout + "\n" + e2e_result.stderr)[-3000:],
            }
        )
        evidence_refs.extend(DEFAULT_WORKER_E2E_TARGETS)

    gate_requirements = [
        {
            "name": "policy-bypass-protection",
            "ok": any(item["name"] == "worker-core-e2e" and item["ok"] for item in checks)
            or any(item["name"] == "worker-unit-tests" and item["ok"] for item in checks),
            "detail": "Denied command and approval-gated paths verified by worker tests.",
        },
        {
            "name": "trace-metadata-required",
            "ok": any("test_worker_trace_metadata.py" in item.get("output_tail", "") or item["ok"] for item in checks),
            "detail": "Trace metadata checks must remain in worker test gate.",
        },
    ]
    checks.extend(
        {
            "name": item["name"],
            "ok": bool(item["ok"]),
            "returncode": 0 if item["ok"] else 2,
            "command": "internal:worker-gate-requirement",
            "output_tail": item["detail"],
        }
        for item in gate_requirements
    )

    redaction_failures: list[str] = []
    for check in checks:
        ok, matches = enforce_redaction_gate(str(check.get("output_tail") or ""))
        if not ok:
            redaction_failures.extend(matches)
    checks.append(
        {
            "name": "worker-redaction-gate",
            "ok": not redaction_failures,
            "returncode": 0 if not redaction_failures else 2,
            "command": "internal:redaction-gate",
            "output_tail": "ok" if not redaction_failures else "\n".join(redaction_failures),
        }
    )

    return {
        "schema": "worker_checks_report.v1",
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "evidence_refs": evidence_refs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run native worker unit and E2E checks.")
    parser.add_argument("--skip-unit", action="store_true")
    parser.add_argument("--skip-e2e", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report = run_worker_checks(skip_unit=args.skip_unit, skip_e2e=args.skip_e2e, strict=args.strict)
    print(f"ok={report['ok']}")
    if report.get("evidence_refs"):
        print("worker_evidence_refs=" + ", ".join(report["evidence_refs"]))

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"worker_report={out_path.relative_to(ROOT)}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
