#!/usr/bin/env python3
"""Validate runbook coverage and ingest explicit live game-day evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
RUNBOOKS = (
    "docs/operations/semantic-media-speech.md",
    "docs/operations/speech-evidence-reconciliation.md",
    "docs/user/semantic-media-speech.md",
    "docs/privacy/semantic-media-speech-consent.md",
)
SCENARIOS = frozenset({"hub-failover", "sfu-failover", "revocation", "worker-drain", "full-feature-rollback"})
SCENARIO_COMMANDS: Mapping[str, tuple[str, ...]] = {
    "hub-failover": (
        "tests/integration/test_semantic_relay_multi_hub.py",
        "tests/test_semantic_lease_repository.py",
    ),
    "sfu-failover": (
        "tests/chaos/test_semantic_sfu_failover.py",
        "tests/test_semantic_sfu_admission.py",
    ),
    "revocation": (
        "tests/e2e/test_speech_evidence_revocation_flow.py",
        "tests/test_speech_evidence_peer_revocation.py",
    ),
    "worker-drain": (
        "tests/test_background_lifecycle_shutdown.py",
        "tests/test_speech_adaptation_dispatcher.py",
        "tests/test_speech_reconciliation_queue_pump.py",
    ),
    "full-feature-rollback": (
        "tests/test_semantic_media_feature_flags.py",
        "tests/test_semantic_media_rollout_policy.py",
    ),
}


def validate_runbooks() -> tuple[str, dict[str, int]]:
    required_terms = {
        "deploy",
        "migration",
        "health",
        "capacity",
        "drain",
        "kill-switch",
        "rekey",
        "revoke",
        "keyrotation",
        "worker-cancel",
        "cleanup",
        "rollback",
        "klartextleak",
        "staleadapter",
        "budgetrunaway",
    }
    joined = "\n".join((ROOT / path).read_text(encoding="utf-8").casefold() for path in RUNBOOKS)
    compact = joined.replace(" ", "").replace("\n", "")
    missing = sorted(term for term in required_terms if term not in compact)
    if missing:
        raise ProgramEvidenceError("semantic_media_runbook_coverage_missing")
    source_paths = tuple(
        sorted(
            {
                *RUNBOOKS,
                "scripts/run_semantic_media_game_day.py",
                *(path for paths in SCENARIO_COMMANDS.values() for path in paths),
            }
        )
    )
    return source_hash(ROOT, source_paths), {"runbook_count": len(RUNBOOKS)}


def execute_local(*, timeout_seconds: int = 300) -> dict[str, Any]:
    """Execute the bounded, repository-local operational game-day matrix.

    Each scenario is a separate process so module singletons cannot leak from
    one failure domain into the next.  These are deterministic operational
    drills, not a claim that a production deployment was failed over.
    """

    source_digest, _ = validate_runbooks()
    environment = dict(os.environ)
    environment["RUN_INTEGRATION_TESTS"] = "1"
    rows: list[dict[str, Any]] = []
    for name in sorted(SCENARIOS):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *SCENARIO_COMMANDS[name]],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
        passed = completed.returncode == 0
        rows.append(
            {
                "name": name,
                "passed": passed,
                "reason_codes": [] if passed else ["game_day_scenario_command_failed"],
                "ordinary_call_healthy": passed,
            }
        )
    return {
        "schema": "ananta.semantic-media-game-day.v1",
        "source_sha256": source_digest,
        "scenarios": rows,
    }


def evaluate_live(report: Mapping[str, Any]) -> GateEvidence:
    source_digest, measurements = validate_runbooks()
    if (
        set(report) != {"schema", "source_sha256", "scenarios"}
        or report.get("schema") != "ananta.semantic-media-game-day.v1"
    ):
        raise ProgramEvidenceError("semantic_media_game_day_contract_invalid")
    if report.get("source_sha256") != source_digest:
        raise ProgramEvidenceError("semantic_media_game_day_source_stale")
    rows = report.get("scenarios")
    if not isinstance(rows, list):
        raise ProgramEvidenceError("semantic_media_game_day_scenarios_invalid")
    names: set[str] = set()
    reasons: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"name", "passed", "reason_codes", "ordinary_call_healthy"}:
            raise ProgramEvidenceError("semantic_media_game_day_scenario_invalid")
        names.add(str(row["name"]))
        if row["passed"] is not True or row["ordinary_call_healthy"] is not True:
            reasons.append("semantic_media_game_day_scenario_failed")
    if names != SCENARIOS:
        reasons.append("semantic_media_game_day_coverage_missing")
    measurements.update({"scenario_count": len(rows), "passed_scenario_count": len(rows) - len(reasons)})
    return GateEvidence(
        "ASMP-QA-011",
        "passed" if not reasons else "failed",
        tuple(sorted(set(reasons))),
        source_digest,
        canonical_sha256(report),
        measurements,
    )


def unavailable() -> GateEvidence:
    source_digest, measurements = validate_runbooks()
    evidence = unavailable_evidence(
        "ASMP-QA-011",
        source_sha256=source_digest,
        config_sha256=canonical_sha256({"scenarios": sorted(SCENARIOS)}),
        reason_code="live_game_day_evidence_unavailable",
    )
    return GateEvidence(
        evidence.gate_id,
        evidence.status,
        evidence.reason_codes,
        evidence.source_sha256,
        evidence.config_sha256,
        {**measurements, **evidence.measurements},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--execute-local", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-output", type=Path)
    args = parser.parse_args()
    try:
        if args.report is not None and args.execute_local:
            raise ProgramEvidenceError("semantic_media_game_day_input_ambiguous")
        report = execute_local() if args.execute_local else (
            json.loads(args.report.read_text(encoding="utf-8")) if args.report is not None else None
        )
        evidence = unavailable() if report is None else evaluate_live(report)
    except (OSError, json.JSONDecodeError, ProgramEvidenceError, subprocess.TimeoutExpired) as exc:
        print(
            json.dumps(
                {"status": "failed", "reason_code": getattr(exc, "reason_code", "game_day_input_invalid")},
                sort_keys=True,
            )
        )
        return 1
    if args.output:
        write_report(args.output, evidence)
    if args.report_output:
        if report is None:
            print(json.dumps({"status": "failed", "reason_code": "game_day_report_unavailable"}, sort_keys=True))
            return 1
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
