"""Hub policy deriving verification completion from commit-bound CI evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class VerificationCompletionDecision:
    complete: bool
    reason_code: str
    run_id: str | None
    commit: str | None
    missing_or_failed_jobs: tuple[str, ...] = ()


class VerificationCompletionPolicy:
    """Fail closed unless every required job is successful on an accepted commit."""

    def evaluate(
        self,
        *,
        expected_commit: str,
        required_jobs: Sequence[str],
        workflow_run: Mapping[str, object] | None,
        equivalent_commits: Sequence[str] = (),
    ) -> VerificationCompletionDecision:
        if not workflow_run:
            return VerificationCompletionDecision(False, "verification_ci_run_missing", None, None)
        run_id = str(workflow_run.get("run_id") or "") or None
        commit = str(workflow_run.get("head_sha") or "") or None
        accepted_commits = {str(expected_commit), *(str(item) for item in equivalent_commits)}
        if commit not in accepted_commits:
            return VerificationCompletionDecision(False, "verification_ci_commit_stale", run_id, commit)
        if workflow_run.get("conclusion") != "success":
            return VerificationCompletionDecision(False, "verification_ci_run_not_successful", run_id, commit)
        raw_jobs = workflow_run.get("jobs")
        jobs = dict(raw_jobs) if isinstance(raw_jobs, Mapping) else {}
        failed = tuple(sorted(name for name in set(required_jobs) if jobs.get(name) != "success"))
        if failed:
            return VerificationCompletionDecision(
                False,
                "verification_ci_required_jobs_incomplete",
                run_id,
                commit,
                failed,
            )
        return VerificationCompletionDecision(True, "verification_ci_evidence_complete", run_id, commit)


__all__ = ["VerificationCompletionDecision", "VerificationCompletionPolicy"]
