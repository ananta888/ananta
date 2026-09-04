"""Immutable revision-pair guard for experimental behavior diffs."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ananta_contracts.verification import VerificationAssignmentV1, VerificationReportV1
from worker.verification.ports import BehaviorDiffRunnerPort


def _git_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed.stdout.strip()


@dataclass(frozen=True, slots=True)
class RevisionPair:
    baseline_root: Path
    patch_root: Path
    baseline_revision: str
    patch_revision: str


class RevisionBoundDiffRunner:
    """Validates existing worktrees and never creates, mutates, or removes one."""

    def __init__(
        self,
        adapter: BehaviorDiffRunnerPort,
        *,
        revision_resolver: Callable[[Path], str] = _git_revision,
    ) -> None:
        self._adapter = adapter
        self._revision_resolver = revision_resolver

    def compare(
        self,
        assignment: VerificationAssignmentV1,
        *,
        revisions: RevisionPair,
        left_symbol: str,
        right_symbol: str,
    ) -> VerificationReportV1:
        baseline = revisions.baseline_root.resolve(strict=True)
        patch = revisions.patch_root.resolve(strict=True)
        if baseline == patch:
            raise ValueError("verification_diff_worktrees_not_distinct")
        if assignment.repository_revision != revisions.patch_revision:
            raise ValueError("verification_diff_assignment_revision_mismatch")
        if self._revision_resolver(baseline) != revisions.baseline_revision:
            raise ValueError("verification_diff_baseline_revision_mismatch")
        if self._revision_resolver(patch) != revisions.patch_revision:
            raise ValueError("verification_diff_patch_revision_mismatch")
        return self._adapter.compare(
            assignment,
            repository=patch,
            left_symbol=left_symbol,
            right_symbol=right_symbol,
        )


__all__ = ["RevisionBoundDiffRunner", "RevisionPair"]
