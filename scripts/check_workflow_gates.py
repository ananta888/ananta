"""WFG-028 quality gate: run the WFG-specific test suite.

This script is the entry point for the CI job that
verifies the workflow-gates layer (WFG-001..028). It
runs the small but high-signal set of tests that the
workflow layer depends on:

  * ``test_workflow_gates_regression.py``  (WFG-026)
  * ``test_workflow_gates_property.py``    (WFG-027)
  * ``test_workflow_*.py``                 (WFG-001..024)
  * ``test_human_approval_service.py``     (WFG-024)
  * ``test_blueprint_workflow_catalog.py`` (WFG-018..021)
  * ``test_blueprint_migration_service.py`` (WFG-021)
  * ``test_workflow_status_api.py``        (WFG-017)
  * ``test_workflow_status_service.py``    (WFG-017)
  * ``test_workflow_event_service.py``     (WFG-015)
  * ``test_workflow_artifact_flow.py``     (WFG-016)
  * ``test_tui_workflow_status_view.py``   (WFG-022)
  * ``test_blueprint_planning_adapter.py`` (WFG-006..009)
  * ``tests/e2e/blueprints/test_gate_decision_flow.py`` (WFG-025)

The script returns exit 0 when every test passes and
exit 1 otherwise. It is intended to be wired into
``scripts/check_pipeline.py`` and into the
``quality-and-docs.yml`` workflow.

Usage::

    python scripts/check_workflow_gates.py
    python scripts/check_workflow_gates.py --fast     # 1 example per test file
    python scripts/check_workflow_gates.py --report path.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_FILES = [
    "tests/test_workflow_gates_regression.py",
    "tests/test_workflow_gates_property.py",
    "tests/test_workflow_event_service.py",
    "tests/test_workflow_artifact_flow.py",
    "tests/test_workflow_status_api.py",
    "tests/test_workflow_status_service.py",
    "tests/test_human_approval_service.py",
    "tests/test_blueprint_workflow_catalog.py",
    "tests/test_blueprint_migration_service.py",
    "tests/test_tui_workflow_status_view.py",
    "tests/test_blueprint_planning_adapter.py",
    "tests/e2e/blueprints/test_gate_decision_flow.py",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Run only one example per test file (for quick smoke checks).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write a JSON report.",
    )
    args = parser.parse_args(argv)

    # Sanity: every test file must exist. A missing
    # file means someone removed a test without
    # updating this gate; we refuse to silently skip
    # it.
    missing: list[str] = []
    for rel in DEFAULT_TEST_FILES:
        if not (REPO_ROOT / rel).is_file():
            missing.append(rel)
    if missing:
        print("WFG-028: missing test files:", file=sys.stderr)
        for rel in missing:
            print(f"  - {rel}", file=sys.stderr)
        return 2

    cmd: list[str] = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--timeout=60",
        "-x",
    ]
    if args.fast:
        cmd += ["--maxfail=1", "-p", "no:cacheprovider"]
    cmd += DEFAULT_TEST_FILES

    print("WFG-028: running workflow-gates quality gate")
    print(" ".join(cmd))
    start = time.time()
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    duration = round(time.time() - start, 2)
    passed = result.returncode == 0

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "passed": passed,
                    "duration_s": duration,
                    "returncode": result.returncode,
                    "test_files": DEFAULT_TEST_FILES,
                    "stdout_tail": result.stdout[-4000:],
                    "stderr_tail": result.stderr[-4000:],
                },
                indent=2,
            )
        )

    if passed:
        print(f"WFG-028: PASSED in {duration}s")
        return 0
    print(f"WFG-028: FAILED (returncode={result.returncode})", file=sys.stderr)
    print("--- stdout tail ---", file=sys.stderr)
    print(result.stdout[-4000:], file=sys.stderr)
    print("--- stderr tail ---", file=sys.stderr)
    print(result.stderr[-4000:], file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
