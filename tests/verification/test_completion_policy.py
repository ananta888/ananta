from __future__ import annotations

import pytest

from agent.services.verification_completion_policy import VerificationCompletionPolicy

REQUIRED = ("hypothesis-matrix", "symbolic-targeted", "worker-image")
COMMIT = "a" * 40


def _run(*, commit: str = COMMIT, conclusion: str = "success", symbolic: str = "success") -> dict:
    return {
        "run_id": "33910210131",
        "head_sha": commit,
        "conclusion": conclusion,
        "jobs": {
            "hypothesis-matrix": "success",
            "symbolic-targeted": symbolic,
            "worker-image": "success",
        },
    }


def test_completion_requires_successful_commit_bound_jobs() -> None:
    decision = VerificationCompletionPolicy().evaluate(
        expected_commit=COMMIT,
        required_jobs=REQUIRED,
        workflow_run=_run(),
    )
    assert (decision.complete, decision.reason_code, decision.run_id) == (
        True,
        "verification_ci_evidence_complete",
        "33910210131",
    )


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        (None, "verification_ci_run_missing"),
        (_run(commit="b" * 40), "verification_ci_commit_stale"),
        (_run(conclusion="failure"), "verification_ci_run_not_successful"),
        (_run(symbolic="skipped"), "verification_ci_required_jobs_incomplete"),
    ],
)
def test_completion_fails_closed_for_missing_stale_failed_or_skipped_evidence(run, reason: str) -> None:
    decision = VerificationCompletionPolicy().evaluate(
        expected_commit=COMMIT,
        required_jobs=REQUIRED,
        workflow_run=run,
    )
    assert decision.complete is False
    assert decision.reason_code == reason
