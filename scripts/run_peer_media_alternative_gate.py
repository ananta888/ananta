#!/usr/bin/env python3
"""Evaluate WebCodecs over an unreliable DataChannel under Hub test evidence."""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.hub_browser_test_evidence import (  # noqa: E402
    HubBrowserTestRun,
    host_environment,
    localhost_origin,
)

FRONTEND = ROOT / "frontend-angular"
TASK_ID = "DPM-POC-003"
SOURCE_PATHS = (
    Path("frontend-angular/playwright.peer-media-alternative.config.ts"),
    Path("frontend-angular/tests/peer-media-alternative.spec.ts"),
    Path("scripts/hub_browser_test_evidence.py"),
    Path("scripts/run_peer_media_alternative_gate.py"),
)
EXPECTED_ENGINES = frozenset({"chromium", "firefox"})
EXPECTED_OUTCOMES = frozenset({"bounded_experiment", "no_go"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-db", type=Path, default=ROOT / "data/peer-media-evidence.sqlite3")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "artifacts/test-gates/peer-media-alternative.json",
    )
    args = parser.parse_args()
    environment = host_environment(frontend=FRONTEND)
    profile = {
        "schema": "ananta.peer-media-alternative-profile.v1",
        "engines": sorted(EXPECTED_ENGINES),
        "transport": {"kind": "rtc_data_channel", "ordered": False, "max_retransmits": 0},
        "headless": True,
        "duration_seconds": 1,
        "production_claim": False,
    }
    reservation = HubBrowserTestRun.reserve(
        root=ROOT,
        registry_db=args.registry_db,
        task_id=TASK_ID,
        source_paths=SOURCE_PATHS,
        execution_profile=profile,
        environment=environment,
    )
    with tempfile.TemporaryDirectory(prefix="ananta-peer-media-alternative-") as measurement_dir:
        child_environment = os.environ.copy()
        child_environment["ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON"] = json.dumps(
            reservation.assignment, sort_keys=True
        )
        child_environment["ANANTA_PEER_MEDIA_ALTERNATIVE_DIR"] = measurement_dir
        cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        with localhost_origin() as origin:
            child_environment["ANANTA_PEER_MEDIA_ORIGIN"] = origin
            try:
                completed = subprocess.run(
                    ("npx", "playwright", "test", "--config", "playwright.peer-media-alternative.config.ts"),
                    cwd=FRONTEND,
                    env=child_environment,
                    check=False,
                    text=True,
                    timeout=180,
                )
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                exit_code = -1
        cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        measurements = _measurements(Path(measurement_dir))
    succeeded = _evaluation_complete(exit_code, measurements)
    decision = (
        "no_go"
        if measurements and all(row.get("decision") == "no_go" for row in measurements)
        else "bounded_experiment"
    )
    payload = {
        "schema": "ananta.peer-media-alternative-result.v1",
        "repository_revision": reservation.repository_revision,
        "status": "passed" if succeeded else "failed",
        "decision": decision if succeeded else "no_go",
        "reason_codes": (
            ["webcodecs_browser_support_missing"]
            if succeeded and decision == "no_go"
            else ["webcodecs_transport_evaluation_bounded"]
            if succeeded and decision == "bounded_experiment"
            else ["webcodecs_transport_evaluation_incomplete"]
        ),
        "measurements": measurements,
        "host_environment": environment,
        "execution_profile": profile,
        "command_exit_code": exit_code,
        "child_cpu_seconds": round(
            cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime,
            6,
        ),
        "human_intervention_required": False,
    }
    evidence = reservation.complete(payload, succeeded=succeeded)
    report = {**payload, "evidence": evidence}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "evidence": evidence}, sort_keys=True))
    return 0 if succeeded and not evidence["production_release_eligible"] else 1


def _measurements(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def _evaluation_complete(exit_code: int, measurements: list[dict[str, Any]]) -> bool:
    return (
        exit_code == 0
        and {row.get("engine") for row in measurements} == EXPECTED_ENGINES
        and all(row.get("decision") in EXPECTED_OUTCOMES for row in measurements)
        and all(row.get("humanInterventionRequired") is False for row in measurements)
    )
if __name__ == "__main__":
    raise SystemExit(main())
