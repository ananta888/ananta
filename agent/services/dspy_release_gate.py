"""Fail-closed DSPy release gate with assignment-bound evidence allowlists."""

from __future__ import annotations

import re
from typing import Any, Mapping

_SOURCE = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")
_RUN = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")


class DspyReleaseGate:
    REQUIRED = frozenset({"schema", "unit", "security", "integration", "reproducibility", "recovery", "rollback"})

    def evaluate(
        self,
        *,
        local_gates: Mapping[str, bool],
        source_refs: list[str],
        run_refs: list[str],
        allowed_source_refs: frozenset[str] = frozenset(),
        allowed_run_refs: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if any(not local_gates.get(key) for key in self.REQUIRED):
            reasons.append("dspy_local_release_gates_incomplete")
        if not source_refs or any(
            not _SOURCE.fullmatch(value) or value not in allowed_source_refs for value in source_refs
        ):
            reasons.append("dspy_source_evidence_unavailable")
        if not run_refs or any(not _RUN.fullmatch(value) or value not in allowed_run_refs for value in run_refs):
            reasons.append("dspy_run_evidence_unavailable")
        return {
            "release_allowed": not reasons,
            "state": "passed" if not reasons else "blocked",
            "reason_codes": reasons,
            "source_refs": list(source_refs) if not reasons else [],
            "run_refs": list(run_refs) if not reasons else [],
            "human_intervention_required": False,
        }


__all__ = ["DspyReleaseGate"]
