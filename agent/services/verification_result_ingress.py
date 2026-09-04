"""Hub-owned, idempotent acceptance of assignment-bound Worker reports."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping

from ananta_contracts.verification import VerificationAssignmentV1, VerificationReportV1, canonical_digest


class VerificationResultIngress:
    def __init__(self, *, lease_is_current: Callable[[str, str, str], bool]) -> None:
        self._lease_is_current = lease_is_current
        self._accepted: dict[str, tuple[str, dict]] = {}

    def accept(
        self,
        assignment: VerificationAssignmentV1,
        raw_report: Mapping[str, object],
    ) -> tuple[dict, bool]:
        report = VerificationReportV1.from_mapping(raw_report)
        evidence = assignment.evidence_assignment
        if not self._lease_is_current(
            str(evidence["task_id"]),
            str(evidence["assignment_id"]),
            str(evidence["dispatch_lease_id"]),
        ):
            raise ValueError("verification_dispatch_lease_stale")
        exact = {
            "assignment_digest": assignment.digest,
            "run_ref": evidence["run_id"],
            "repository_revision": assignment.repository_revision,
            "profile_id": assignment.profile_id,
            "profile_digest": assignment.profile_digest,
            "toolchain_digest": assignment.toolchain_digest,
            "backend": assignment.backend,
            "target_symbols": list(assignment.target_symbols),
        }
        actual = report.to_dict()
        for field, expected in exact.items():
            if actual[field] != expected:
                raise ValueError(f"verification_result_{field}_mismatch")
        digest = canonical_digest(actual)
        run_ref = report.run_ref
        previous = self._accepted.get(run_ref)
        if previous:
            if not hmac.compare_digest(previous[0], digest):
                raise ValueError("verification_result_conflict")
            return dict(previous[1]), False
        self._accepted[run_ref] = (digest, actual)
        return dict(actual), True


__all__ = ["VerificationResultIngress"]
