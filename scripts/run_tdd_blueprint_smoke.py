from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SMOKE_TEST = "tests/e2e/blueprints/test_tdd_tiny_repo_flow.py"


def _python_has_pytest(python_exec: str) -> bool:
    probe = subprocess.run(
        [python_exec, "-c", "import pytest"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists() and _python_has_pytest(str(venv_python)):
        return str(venv_python)
    if _python_has_pytest(sys.executable):
        return sys.executable
    return sys.executable


def _resolve_output_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return candidate


def _run_command(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, check=False, env=env)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_refs(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _validate_smoke_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    claims = report.get("claims") or {}
    phases = report.get("phases") or {}
    red_phase = phases.get("red") or {}
    patch_phase = phases.get("patch") or {}
    green_phase = phases.get("green") or {}
    degraded_phase = phases.get("degraded") or {}
    evidence_refs = _normalize_refs(report.get("evidence_refs"))
    checks: list[dict[str, Any]] = []

    green_claimed = bool(claims.get("green_phase_claimed"))
    green_status = str(green_phase.get("status") or "").strip().lower()
    green_ok = (not green_claimed) or green_status == "green_passed"
    checks.append(
        {
            "name": "tdd-green-phase-evidence",
            "ok": green_ok,
            "detail": f"green_claimed={green_claimed}; green_status={green_status or '<missing>'}",
        }
    )

    red_claimed = bool(claims.get("red_phase_claimed"))
    red_status = str(red_phase.get("status") or "").strip().lower()
    degraded_reason = str(degraded_phase.get("reason") or "").strip()
    red_ok = red_status == "red_expected" if red_claimed else bool(degraded_reason)
    checks.append(
        {
            "name": "tdd-red-phase-or-degraded",
            "ok": red_ok,
            "detail": (
                f"red_claimed={red_claimed}; red_status={red_status or '<missing>'}; "
                f"degraded_reason_present={bool(degraded_reason)}"
            ),
        }
    )

    red_ref = str(red_phase.get("evidence_path") or "").strip()
    patch_ref = str(patch_phase.get("evidence_path") or "").strip()
    green_ref = str(green_phase.get("evidence_path") or "").strip()
    order_ok = all([red_ref, patch_ref, green_ref]) and all(
        ref in evidence_refs for ref in (red_ref, patch_ref, green_ref)
    )
    if order_ok:
        order_ok = evidence_refs.index(red_ref) < evidence_refs.index(patch_ref) < evidence_refs.index(green_ref)
    checks.append(
        {
            "name": "tdd-evidence-order",
            "ok": order_ok,
            "detail": "red->patch->green evidence sequence must be preserved",
        }
    )
    checks.append(
        {
            "name": "tdd-evidence-refs-present",
            "ok": bool(evidence_refs),
            "detail": f"evidence_ref_count={len(evidence_refs)}",
        }
    )
    return checks


def run_tdd_blueprint_smoke(
    *,
    smoke_test: str = DEFAULT_SMOKE_TEST,
    out: str = "artifacts/tdd/tdd_blueprint_smoke_report.json",
    strict: bool = False,
) -> dict[str, Any]:
    out_path = _resolve_output_path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TDD_SMOKE_REPORT_PATH"] = str(out_path)
    python_exec = _python_executable()
    command = [python_exec, "-m", "pytest", "-q", smoke_test]
    test_result = _run_command(command, env=env)
    checks: list[dict[str, Any]] = [
        {
            "name": "tdd-smoke-pytest",
            "ok": test_result.returncode == 0,
            "detail": " ".join(command),
            "output_tail": (test_result.stdout + "\n" + test_result.stderr)[-3000:],
        }
    ]

    smoke_report: dict[str, Any] = {}
    if test_result.returncode == 0:
        if out_path.exists():
            try:
                smoke_report = _load_json(out_path)
            except (OSError, json.JSONDecodeError):
                checks.append(
                    {
                        "name": "tdd-smoke-report-json",
                        "ok": False,
                        "detail": "generated smoke report is missing or invalid json",
                    }
                )
        else:
            checks.append(
                {
                    "name": "tdd-smoke-report-json",
                    "ok": False,
                    "detail": "smoke report was not generated by e2e flow",
                }
            )

    if smoke_report:
        checks.extend(_validate_smoke_report(smoke_report))

    ok = all(bool(check.get("ok")) for check in checks)
    if strict and not smoke_report:
        ok = False
    evidence_refs = _normalize_refs(smoke_report.get("evidence_refs"))
    final_report = dict(smoke_report)
    final_report.update(
        {
            "schema": "tdd_smoke_gate_report.v1",
            "ok": ok,
            "smoke_test": smoke_test,
            "command": " ".join(command),
            "checks": checks,
            "evidence_refs": evidence_refs,
        }
    )
    out_path.write_text(json.dumps(final_report, indent=2) + "\n", encoding="utf-8")
    return final_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic TDD tiny-repo smoke and emit gate report.")
    parser.add_argument("--smoke-test", default=DEFAULT_SMOKE_TEST)
    parser.add_argument("--out", default="artifacts/tdd/tdd_blueprint_smoke_report.json")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = run_tdd_blueprint_smoke(smoke_test=args.smoke_test, out=args.out, strict=args.strict)
    print(f"ok={report['ok']}")
    if report.get("evidence_refs"):
        print("tdd_evidence_refs=" + ", ".join(report["evidence_refs"]))
    out_path = _resolve_output_path(args.out)
    report_label = out_path.relative_to(ROOT) if ROOT in out_path.parents else out_path
    print(f"tdd_report={report_label}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
