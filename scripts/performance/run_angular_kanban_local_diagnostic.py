#!/usr/bin/env python3
"""Run and persist the existing Angular Kanban Playwright diagnostic."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend-angular"
DEFAULT_OUTPUT = ROOT / "artifacts" / "angular-kanban-local-performance-diagnostic.json"
MARKER = "KANBAN_LOCAL_DIAGNOSTIC "
EXPECTED_SCHEMA = "ananta.kanban-performance-local-diagnostic.v1"
LOCAL_SCOPE = "local_diagnostic_not_release_evidence"


class AngularDiagnosticError(RuntimeError):
    pass


def parse_diagnostic_marker(output: str) -> dict[str, Any]:
    positions: list[int] = []
    offset = 0
    while True:
        found = output.find(MARKER, offset)
        if found < 0:
            break
        positions.append(found + len(MARKER))
        offset = found + len(MARKER)
    if len(positions) != 1:
        raise AngularDiagnosticError("angular_diagnostic_marker_count_invalid")
    try:
        payload, _end = json.JSONDecoder().raw_decode(output[positions[0] :])
    except json.JSONDecodeError as exc:
        raise AngularDiagnosticError("angular_diagnostic_marker_json_invalid") from exc
    if not isinstance(payload, dict):
        raise AngularDiagnosticError("angular_diagnostic_marker_payload_invalid")
    validate_local_report(payload)
    return payload


def validate_local_report(report: Mapping[str, Any]) -> None:
    dataset = report.get("dataset")
    if report.get("schema") != EXPECTED_SCHEMA:
        raise AngularDiagnosticError("angular_diagnostic_schema_invalid")
    if report.get("evidence_classification") != LOCAL_SCOPE:
        raise AngularDiagnosticError("angular_diagnostic_scope_invalid")
    if report.get("formal") is not False or report.get("release_evidence") is not False:
        raise AngularDiagnosticError("angular_diagnostic_must_remain_local")
    if not isinstance(dataset, Mapping):
        raise AngularDiagnosticError("angular_diagnostic_dataset_invalid")
    expected = {
        "cards": 1000,
        "view_groups": 10,
        "status_groups": 10,
        "canonical_columns": 4,
    }
    if any(dataset.get(key) != value for key, value in expected.items()):
        raise AngularDiagnosticError("angular_diagnostic_workload_invalid")
    viewports = report.get("viewports")
    if not isinstance(viewports, list) or len(viewports) != 2:
        raise AngularDiagnosticError("angular_diagnostic_viewports_invalid")


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AngularDiagnosticError("angular_diagnostic_timeout") from exc
    if result.returncode != 0:
        tail = (result.stdout + "\n" + result.stderr)[-4000:]
        raise AngularDiagnosticError(
            f"angular_diagnostic_failed:{result.returncode}:{tail}"
        )
    return result


def collect_runtime_metadata(timeout_seconds: float) -> dict[str, Any]:
    node = _run(
        ["node", "--version"],
        cwd=FRONTEND,
        timeout_seconds=min(timeout_seconds, 15.0),
    ).stdout.strip()
    playwright = _run(
        [str(FRONTEND / "node_modules" / ".bin" / "playwright"), "--version"],
        cwd=FRONTEND,
        timeout_seconds=min(timeout_seconds, 15.0),
    ).stdout.strip()
    browser_script = (
        "const {chromium}=require('playwright');"
        "(async()=>{const b=await chromium.launch({headless:true});"
        "process.stdout.write(JSON.stringify({name:'chromium',version:b.version()}));"
        "await b.close();})().catch(e=>{console.error(e);process.exit(1);});"
    )
    browser_raw = _run(
        ["node", "-e", browser_script],
        cwd=FRONTEND,
        timeout_seconds=min(timeout_seconds, 30.0),
    ).stdout
    try:
        browser = json.loads(browser_raw)
    except json.JSONDecodeError as exc:
        raise AngularDiagnosticError("angular_browser_metadata_invalid") from exc
    if (
        not node
        or not playwright
        or not isinstance(browser, dict)
        or not browser.get("name")
        or not browser.get("version")
    ):
        raise AngularDiagnosticError("angular_runtime_metadata_incomplete")
    return {
        "node": {"version": node},
        "playwright": {"version": playwright},
        "browser": {
            "name": str(browser["name"]),
            "version": str(browser["version"]),
        },
    }


def run_diagnostic(*, timeout_seconds: float) -> dict[str, Any]:
    playwright = FRONTEND / "node_modules" / ".bin" / "playwright"
    if not playwright.is_file():
        raise AngularDiagnosticError("angular_playwright_not_installed")
    result = _run(
        [
            str(playwright),
            "test",
            "tests/kanban-performance.local.spec.ts",
            "--config",
            "playwright.kanban-performance.config.ts",
        ],
        cwd=FRONTEND,
        timeout_seconds=timeout_seconds,
    )
    report = parse_diagnostic_marker(result.stdout + "\n" + result.stderr)
    report["producer_runtime"] = collect_runtime_metadata(timeout_seconds)
    return report


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    report = run_diagnostic(timeout_seconds=max(30.0, args.timeout_seconds))
    write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "schema": report["schema"],
                "status": "passed_local_diagnostic",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
