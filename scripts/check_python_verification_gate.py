#!/usr/bin/env python3
"""Validate commit-bound Python verification gate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

SCHEMA = "ananta.python-verification-gate.v2"
REQUIRED_JOBS = frozenset(
    {
        "core-boundary",
        "repeated-fast-gate",
        "symbolic-targeted",
        "worker-image",
        "hypothesis-matrix (3.10)",
        "hypothesis-matrix (3.11)",
        "hypothesis-matrix (3.12)",
        "hypothesis-matrix (3.13)",
        "hypothesis-matrix (3.14)",
    }
)
REQUIRED_CAPABILITY_DECISIONS = frozenset(
    {
        "hypothesis_core",
        "hypothesis_stateful",
        "crosshair_check",
        "crosshair_cover",
        "hypothesis_crosshair_backend",
        "crosshair_diffbehavior",
        "pynguin",
        "qodo_cover",
    }
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def stable_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_gate(payload: Mapping[str, object], *, expected_commit: str | None = None) -> tuple[str, ...]:
    reasons: set[str] = set()
    if payload.get("schema") != SCHEMA:
        reasons.add("verification_gate_schema_invalid")
    if payload.get("evidence_classification") != "github_ci_and_local_test_observations":
        reasons.add("verification_gate_classification_invalid")
    if payload.get("production_run_ref") is not None:
        reasons.add("verification_gate_production_identity_invalid")

    ci = payload.get("ci_evidence")
    if not isinstance(ci, Mapping):
        reasons.add("verification_gate_ci_evidence_missing")
        ci = {}
    head_sha = ci.get("head_sha")
    if not isinstance(head_sha, str) or _SHA.fullmatch(head_sha) is None:
        reasons.add("verification_gate_commit_invalid")
    if expected_commit is not None and head_sha != expected_commit:
        reasons.add("verification_gate_commit_stale")
    if ci.get("workflow") != "Python Verification" or ci.get("conclusion") != "success":
        reasons.add("verification_gate_workflow_not_successful")
    run_id = ci.get("run_id")
    if type(run_id) is not int or run_id <= 0:
        reasons.add("verification_gate_ci_run_invalid")
    jobs = ci.get("jobs")
    if not isinstance(jobs, Mapping) or any(jobs.get(name) != "success" for name in REQUIRED_JOBS):
        reasons.add("verification_gate_required_jobs_incomplete")
    if ci.get("jobs_digest") != stable_digest(jobs):
        reasons.add("verification_gate_jobs_digest_mismatch")

    local_gates = payload.get("local_gates")
    if not isinstance(local_gates, list) or not local_gates:
        reasons.add("verification_gate_local_results_missing")
    elif any(not isinstance(gate, Mapping) or gate.get("status") != "passed" for gate in local_gates):
        reasons.add("verification_gate_local_result_failed")

    decisions = payload.get("capability_decisions")
    if not isinstance(decisions, Mapping) or set(decisions) != REQUIRED_CAPABILITY_DECISIONS:
        reasons.add("verification_gate_capability_decisions_incomplete")

    digest = payload.get("content_digest")
    content = {key: value for key, value in payload.items() if key != "content_digest"}
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None or digest != stable_digest(content):
        reasons.add("verification_gate_content_digest_mismatch")
    return tuple(sorted(reasons))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "artifact",
        type=Path,
        nargs="?",
        default=Path("artifacts/test-gates/python-verification-pilot.json"),
    )
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    reasons = validate_gate(payload, expected_commit=args.expected_commit)
    print(json.dumps({"status": "passed" if not reasons else "failed", "reasons": reasons}, sort_keys=True))
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
