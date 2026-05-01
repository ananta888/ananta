from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "e2e"
DEFAULT_E2E_TEST_TARGETS = [
    "tests/e2e/test_core_golden_path.py",
    "tests/e2e/test_freecad_runtime_golden_path.py",
    "tests/e2e/test_cli_golden_path_snapshots.py",
    "tests/e2e/test_cli_degraded_policy_snapshots.py",
    "tests/e2e/test_tui_scripted_smoke.py",
    "tests/e2e/test_web_ui_screenshots.py",
    "tests/e2e/test_rag_dogfood_tiny_repo.py",
    "tests/e2e/test_policy_approval_visual_evidence.py",
    "tests/e2e/test_visual_evidence_redaction.py",
    "tests/e2e/test_e2e_report_generation.py",
]


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(ROOT), check=False, capture_output=True, text=True)


def _validate_required_evidence(aggregate_report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for flow in list(aggregate_report.get("flows") or []):
        flow_id = str(flow.get("flow_id", "unknown"))
        blocking = bool(flow.get("blocking"))
        status = str(flow.get("status", ""))
        if blocking and status == "failed":
            problems.append(f"{flow_id}: blocking flow failed")
        if blocking and not list(flow.get("logs") or []):
            problems.append(f"{flow_id}: missing blocking logs")
        if blocking and not list(flow.get("snapshots") or []):
            problems.append(f"{flow_id}: missing blocking snapshots")
    return problems


def run_e2e_dogfood_checks(*, artifact_root: Path, skip_tests: bool = False) -> dict[str, Any]:
    python_exec = _python_executable()
    checks: list[dict[str, Any]] = []

    if not skip_tests:
        pytest_cmd = [python_exec, "-m", "pytest", "-q", *DEFAULT_E2E_TEST_TARGETS]
        pytest_result = _run_command(pytest_cmd)
        checks.append(
            {
                "name": "pytest-e2e-dogfood",
                "ok": pytest_result.returncode == 0,
                "returncode": pytest_result.returncode,
                "command": " ".join(pytest_cmd),
                "output_tail": (pytest_result.stdout + "\n" + pytest_result.stderr)[-3000:],
            }
        )
        if pytest_result.returncode != 0:
            return {"schema": "e2e_dogfood_checks_v1", "ok": False, "checks": checks, "aggregate_report_path": ""}

    artifact_root.mkdir(parents=True, exist_ok=True)
    aggregate_json_rel = Path("artifacts/e2e/aggregate_report.json")
    aggregate_md_rel = Path("artifacts/e2e/aggregate_report.md")
    report_cmd = [
        python_exec,
        "scripts/e2e/generate_e2e_report.py",
        "--artifact-root",
        str(artifact_root),
        "--out-json",
        str(aggregate_json_rel),
        "--out-md",
        str(aggregate_md_rel),
    ]
    report_result = _run_command(report_cmd)
    checks.append(
        {
            "name": "generate-e2e-report",
            "ok": report_result.returncode == 0,
            "returncode": report_result.returncode,
            "command": " ".join(report_cmd),
            "output_tail": (report_result.stdout + "\n" + report_result.stderr)[-3000:],
        }
    )
    if report_result.returncode != 0:
        return {"schema": "e2e_dogfood_checks_v1", "ok": False, "checks": checks, "aggregate_report_path": ""}

    aggregate_path = ROOT / aggregate_json_rel
    aggregate_report = json.loads(aggregate_path.read_text(encoding="utf-8"))
    evidence_problems = _validate_required_evidence(aggregate_report)
    checks.append(
        {
            "name": "required-evidence",
            "ok": not evidence_problems,
            "returncode": 0 if not evidence_problems else 2,
            "command": "internal:required-evidence-validation",
            "output_tail": "\n".join(evidence_problems) if evidence_problems else "ok",
        }
    )

    summary = dict(aggregate_report.get("summary") or {})
    key_refs: list[str] = []
    for flow in list(aggregate_report.get("flows") or [])[:3]:
        key_refs.extend(list(flow.get("snapshots") or [])[:1])
        key_refs.extend(list(flow.get("screenshots") or [])[:1])

    return {
        "schema": "e2e_dogfood_checks_v1",
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "aggregate_report_path": str(aggregate_json_rel),
        "aggregate_markdown_path": str(aggregate_md_rel),
        "summary": summary,
        "key_evidence_refs": key_refs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run E2E dogfood checks and aggregate visual evidence report.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    artifact_root = Path(args.artifact_root)
    if not artifact_root.is_absolute():
        artifact_root = ROOT / artifact_root

    report = run_e2e_dogfood_checks(artifact_root=artifact_root, skip_tests=args.skip_tests)
    print(f"ok={report['ok']}")
    print(f"aggregate_report_path={report.get('aggregate_report_path', '')}")
    if report.get("key_evidence_refs"):
        print("key_evidence_refs=" + ", ".join(report["key_evidence_refs"]))

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
