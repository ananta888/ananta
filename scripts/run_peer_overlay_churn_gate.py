#!/usr/bin/env python3
"""Run the five-device browser churn gate under Hub-issued test evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.hub_browser_test_evidence import (  # noqa: E402
    HubBrowserTestRun,
    host_environment,
    localhost_origin,
)

FRONTEND = ROOT / "frontend-angular"
TASK_ID = "DPM-QA-002"
EXPECTED_ENGINES = frozenset({"chromium", "firefox"})
DEPENDENCY_REPORTS = {
    "four_peer_mesh": ROOT / "artifacts/test-gates/peer-mesh-browser-capacity.json",
    "nat_turn_matrix": ROOT / "artifacts/test-gates/peer-nat-matrix.json",
}
SOURCE_PATHS = (
    Path("AGENTS.md"),
    Path("frontend-angular/playwright.peer-overlay-churn.config.ts"),
    Path("frontend-angular/tests/peer-overlay-churn.spec.ts"),
    Path("scripts/hub_browser_test_evidence.py"),
    Path("scripts/run_peer_overlay_churn_gate.py"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-db", type=Path, default=ROOT / "data/peer-overlay-churn-evidence.sqlite3")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "artifacts/test-gates/peer-overlay-churn.json"
    )
    args = parser.parse_args()
    environment = host_environment(frontend=FRONTEND)
    profile = {
        "schema": "ananta.peer-overlay-churn-browser-profile.v1",
        "engines": sorted(EXPECTED_ENGINES),
        "device_processes_per_engine": 5,
        "edges": 6,
        "headless": True,
        "recovery_bounds_ms": {
            "background_tab": 2_000,
            "relay_failure": 2_000,
            "browser_crash": 2_000,
            "ice_restart": 5_000,
        },
        "production_claim": False,
    }
    dependencies = {name: _read_report(path) for name, path in DEPENDENCY_REPORTS.items()}
    reservation = HubBrowserTestRun.reserve(
        root=ROOT,
        registry_db=args.registry_db,
        task_id=TASK_ID,
        source_paths=SOURCE_PATHS,
        execution_profile=profile,
        environment=environment,
    )
    child_environment = os.environ.copy()
    measurements: list[dict[str, Any]] = []
    exit_code = -1
    failure_reason = ""
    try:
        with tempfile.TemporaryDirectory(prefix="ananta-peer-churn-") as measurement_dir:
            child_environment.update(
                {
                    "ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON": json.dumps(
                        reservation.assignment, sort_keys=True
                    ),
                    "ANANTA_PEER_CHURN_MEASUREMENT_DIR": measurement_dir,
                }
            )
            with localhost_origin() as origin:
                child_environment["ANANTA_PEER_CHURN_ORIGIN"] = origin
                completed = subprocess.run(
                    ("npx", "playwright", "test", "--config", "playwright.peer-overlay-churn.config.ts"),
                    cwd=FRONTEND,
                    env=child_environment,
                    check=False,
                    text=True,
                    timeout=240,
                )
            exit_code = completed.returncode
            measurements = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(Path(measurement_dir).glob("*.json"))
            ]
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        failure_reason = str(exc).split(":", 1)[0][:120] or type(exc).__name__
    dependency_claims = {
        name: _dependency_claim(report) for name, report in dependencies.items()
    }
    succeeded = (
        not failure_reason
        and exit_code == 0
        and _measurements_complete(measurements, profile["recovery_bounds_ms"])
        and all(claim["valid_test_evidence"] for claim in dependency_claims.values())
    )
    payload = {
        "schema": "ananta.peer-overlay-churn-result.v1",
        "repository_revision": reservation.repository_revision,
        "status": "passed" if succeeded else "failed",
        "decision": "test_gate_passed_production_no_go" if succeeded else "no_go",
        "reason_codes": [] if succeeded else [failure_reason or "peer_overlay_churn_incomplete"],
        "measurements": measurements,
        "capacity_claims": {
            **dependency_claims,
            "five_peer_overlay": {
                "run_id": reservation.run_id,
                "source_id": reservation.source_id,
                "participant_count": 5,
                "evidence_scope": "test",
                "production_release_eligible": False,
            },
        },
        "execution_profile": profile,
        "host_environment": environment,
        "command_exit_code": exit_code,
        "human_intervention_required": False,
        "production_release_eligible": False,
    }
    evidence = reservation.complete(payload, succeeded=succeeded)
    report = {**payload, "evidence": evidence}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason_codes": report["reason_codes"]}, sort_keys=True))
    return 0 if succeeded and not evidence["production_release_eligible"] else 1


def _read_report(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("peer_overlay_dependency_report_invalid")
    return value


def _dependency_claim(report: Mapping[str, Any]) -> dict[str, Any]:
    evidence = dict(report.get("evidence") or {})
    valid = bool(
        report.get("status") == "passed"
        and evidence.get("issuer") == "hub-evidence-registry"
        and str(evidence.get("run_id") or "").startswith("RUN_")
        and str(evidence.get("source_id") or "").startswith("SRC_")
        and evidence.get("scope") == "test"
        and evidence.get("synthetic") is True
        and evidence.get("production_release_eligible") is False
    )
    return {
        "run_id": evidence.get("run_id"),
        "source_id": evidence.get("source_id"),
        "repository_revision": report.get("repository_revision"),
        "valid_test_evidence": valid,
        "production_release_eligible": False,
    }


def _measurements_complete(
    measurements: list[dict[str, Any]], bounds: Mapping[str, int]
) -> bool:
    if {row.get("engine") for row in measurements} != EXPECTED_ENGINES:
        return False
    for row in measurements:
        if len(set(row.get("deviceIdentities") or [])) < 5 or row.get("processIsolation") is not True:
            return False
        scenarios = dict(row.get("scenarios") or {})
        checks = {
            "backgroundTab": "background_tab",
            "relayFailure": "relay_failure",
            "browserCrash": "browser_crash",
            "iceRestart": "ice_restart",
        }
        if any(
            name not in scenarios
            or int(scenarios[name].get("recoveryMs", bounds[bound] + 1)) > bounds[bound]
            for name, bound in checks.items()
        ):
            return False
        if scenarios["backgroundTab"].get("visibility") != "hidden":
            return False
        if scenarios["iceRestart"].get("delivered") is not True:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
