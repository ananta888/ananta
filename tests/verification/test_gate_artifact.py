from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.check_python_verification_gate import (
    REQUIRED_CAPABILITY_DECISIONS,
    REQUIRED_JOBS,
    SCHEMA,
    stable_digest,
    validate_gate,
)

COMMIT = "a" * 40


def _gate() -> dict:
    jobs = {name: "success" for name in REQUIRED_JOBS}
    payload = {
        "schema": SCHEMA,
        "observed_on": "2026-09-04",
        "evidence_classification": "github_ci_and_local_test_observations",
        "production_run_ref": None,
        "ci_evidence": {
            "workflow": "Python Verification",
            "run_id": 123,
            "head_sha": COMMIT,
            "conclusion": "success",
            "jobs": jobs,
            "jobs_digest": stable_digest(jobs),
        },
        "local_gates": [{"name": "unit", "status": "passed"}],
        "capability_decisions": {name: "hold" for name in REQUIRED_CAPABILITY_DECISIONS},
        "note": "bounded test observation, not production evidence",
    }
    payload["content_digest"] = stable_digest(payload)
    return payload


def test_gate_accepts_complete_commit_bound_ci_and_local_evidence() -> None:
    assert validate_gate(_gate(), expected_commit=COMMIT) == ()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda gate: gate["ci_evidence"].update(head_sha="b" * 40), "verification_gate_commit_stale"),
        (
            lambda gate: gate["ci_evidence"]["jobs"].update({"worker-image": "skipped"}),
            "verification_gate_required_jobs_incomplete",
        ),
        (lambda gate: gate.update(production_run_ref="RUN_invented"), "verification_gate_production_identity_invalid"),
        (lambda gate: gate["local_gates"][0].update(status="failed"), "verification_gate_local_result_failed"),
    ],
)
def test_gate_fails_closed_for_stale_incomplete_or_misclassified_evidence(mutation, reason: str) -> None:
    gate = deepcopy(_gate())
    mutation(gate)
    assert reason in validate_gate(gate, expected_commit=COMMIT)


def test_gate_rejects_post_hoc_content_or_job_mutation() -> None:
    gate = _gate()
    gate["ci_evidence"]["jobs"]["core-boundary"] = "failure"
    reasons = validate_gate(gate, expected_commit=COMMIT)
    assert "verification_gate_jobs_digest_mismatch" in reasons
    assert "verification_gate_content_digest_mismatch" in reasons
