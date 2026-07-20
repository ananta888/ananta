#!/usr/bin/env python3
"""Validate the program-wide security/privacy matrix and emit bound evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "docs/security/semantic-media-speech-test-matrix.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/semantic-media-security.json"
THREATS = frozenset(
    {
        "mitm",
        "key-substitution",
        "downgrade",
        "replay",
        "sybil",
        "idor",
        "oversize",
        "dos",
        "poisoning",
        "collusion",
        "stale-lease",
        "stale-consent",
        "data-leak",
    }
)
PHASES = frozenset(
    {
        "buffering",
        "relay",
        "reassembly",
        "curation",
        "dataset",
        "reconciliation",
        "training",
        "evaluation",
        "adapter",
        "inference",
        "export",
        "delete",
    }
)
CASE_FIELDS = frozenset(
    {
        "id",
        "threat",
        "risk",
        "phases",
        "prevention",
        "detection",
        "recovery",
        "audit",
        "test_reference",
        "evidence",
        "release_blocking",
    }
)


def evaluate_matrix(
    matrix: Mapping[str, Any],
    *,
    root: Path = ROOT,
    execution: Mapping[str, Any] | None = None,
) -> tuple[GateEvidence, dict[str, Any]]:
    reasons: list[str] = []
    rows = matrix.get("cases")
    if matrix.get("schema") != "ananta.semantic-media-speech-security-matrix.v1" or not isinstance(rows, list):
        rows = []
        reasons.append("security_matrix_contract_invalid")
    seen: set[str] = set()
    covered_threats: set[str] = set()
    covered_phases: set[str] = set()
    automated = 0
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != CASE_FIELDS:
            reasons.append("security_matrix_case_shape_invalid")
            continue
        case_id = str(raw["id"])
        if case_id in seen:
            reasons.append("security_matrix_case_duplicate")
        seen.add(case_id)
        covered_threats.add(str(raw["threat"]))
        phases = raw["phases"] if isinstance(raw["phases"], list) else []
        covered_phases.update(str(phase) for phase in phases)
        if not all(raw.get(field) for field in ("prevention", "detection", "recovery", "audit")):
            reasons.append("security_matrix_control_missing")
        reference = Path(str(raw["test_reference"]))
        if reference.is_absolute() or ".." in reference.parts or not (root / reference).is_file():
            reasons.append("security_matrix_test_reference_missing")
        if raw.get("risk") in {"critical", "high"}:
            if raw.get("evidence") != "automated" or raw.get("release_blocking") is not True:
                reasons.append("security_matrix_blocking_evidence_missing")
            else:
                automated += 1
    if not THREATS <= covered_threats:
        reasons.append("security_matrix_threat_coverage_missing")
    if not PHASES <= covered_phases:
        reasons.append("security_matrix_phase_coverage_missing")
    config_digest = canonical_sha256(matrix)
    referenced_sources = tuple(
        sorted(
            {
                str(raw["test_reference"])
                for raw in rows
                if isinstance(raw, Mapping)
                and isinstance(raw.get("test_reference"), str)
                and (root / str(raw["test_reference"])).is_file()
            }
        )
    )
    source_digest = source_hash(
        root,
        (
            "agent/services/semantic_media_program_evidence.py",
            "docs/security/semantic-media-speech-test-matrix.v1.json",
            "scripts/run_semantic_media_security_gate.py",
            *referenced_sources,
        ),
    )
    summary = {
        "case_count": len(rows),
        "automated_blocking_cases": automated,
        "covered_threat_count": len(covered_threats & THREATS),
        "covered_phase_count": len(covered_phases & PHASES),
        "referenced_test_file_count": len(referenced_sources),
    }
    if execution is not None:
        summary.update(
            {
                "executed_test_file_count": int(
                    execution.get("test_file_count", 0)
                ),
                "test_exit_code": int(execution.get("exit_code", -1)),
                "test_duration_ms": int(execution.get("duration_ms", 0)),
            }
        )
        if execution.get("status") != "passed":
            reasons.append(str(execution.get("reason_code") or "security_matrix_automated_tests_failed"))
    evidence = GateEvidence(
        gate_id="ASMP-QA-001",
        status="passed" if not reasons else "failed",
        reason_codes=tuple(sorted(set(reasons))),
        source_sha256=source_digest,
        config_sha256=config_digest,
        measurements=summary,
    )
    return evidence, summary


def execute_automated_evidence(
    matrix: Mapping[str, Any],
    *,
    root: Path = ROOT,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Run every unique automated, release-blocking matrix reference."""

    rows = matrix.get("cases")
    if not isinstance(rows, list):
        return {
            "status": "failed",
            "reason_code": "security_matrix_contract_invalid",
            "test_file_count": 0,
            "exit_code": -1,
            "duration_ms": 0,
        }
    references = tuple(
        sorted(
            {
                str(row.get("test_reference"))
                for row in rows
                if isinstance(row, Mapping)
                if row.get("evidence") == "automated"
                and row.get("release_blocking") is True
                and isinstance(row.get("test_reference"), str)
            }
        )
    )
    if not references or any(
        Path(reference).is_absolute()
        or ".." in Path(reference).parts
        or not (root / reference).is_file()
        for reference in references
    ):
        return {
            "status": "failed",
            "reason_code": "security_matrix_test_reference_missing",
            "test_file_count": len(references),
            "exit_code": -1,
            "duration_ms": 0,
        }
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *references],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
        exit_code = int(completed.returncode)
        reason_code = (
            "security_matrix_automated_tests_failed" if exit_code else None
        )
    except subprocess.TimeoutExpired:
        exit_code = -1
        reason_code = "security_matrix_automated_tests_timeout"
    return {
        "status": "passed" if exit_code == 0 else "failed",
        "reason_code": reason_code,
        "test_file_count": len(references),
        "exit_code": exit_code,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
        execution = execute_automated_evidence(matrix)
        evidence, _summary = evaluate_matrix(matrix, execution=execution)
    except (OSError, json.JSONDecodeError, ProgramEvidenceError) as exc:
        print(
            json.dumps(
                {"status": "failed", "reason_code": getattr(exc, "reason_code", "security_gate_input_invalid")},
                sort_keys=True,
            )
        )
        return 1
    if args.output:
        write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True, ensure_ascii=False))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
