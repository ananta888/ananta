"""Fail-closed release decision for concrete agent-safety deployments."""

from __future__ import annotations

import re
from typing import Any, Mapping

_SOURCE_REF = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")
_RUN_REF = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")


class AgentSafetyReleaseGate:
    REQUIRED_LOCAL_GATES = frozenset({"contracts", "security", "chaos", "api", "frontend"})

    def evaluate(
        self,
        *,
        local_gates: Mapping[str, bool],
        containment_available: bool,
        source_refs: list[str],
        run_refs: list[str],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        missing_local = sorted(gate for gate in self.REQUIRED_LOCAL_GATES if not bool(local_gates.get(gate)))
        if missing_local:
            reasons.append("agent_safety_local_gates_incomplete")
        if not containment_available:
            reasons.append("agent_safety_containment_adapter_unavailable")
        if not source_refs or any(not _SOURCE_REF.fullmatch(value) for value in source_refs):
            reasons.append("agent_safety_authoritative_source_evidence_unavailable")
        if not run_refs or any(not _RUN_REF.fullmatch(value) for value in run_refs):
            reasons.append("agent_safety_runtime_evidence_unavailable")
        return {
            "release_allowed": not reasons,
            "state": "passed" if not reasons else "blocked",
            "reason_codes": reasons,
            "local_gates": {key: bool(local_gates.get(key)) for key in sorted(self.REQUIRED_LOCAL_GATES)},
            "containment_available": bool(containment_available),
            "source_refs": list(source_refs) if not reasons else [],
            "run_refs": list(run_refs) if not reasons else [],
            "human_intervention_required": False,
        }


__all__ = ["AgentSafetyReleaseGate"]
