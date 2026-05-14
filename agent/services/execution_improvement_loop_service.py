from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_TERMINAL_STATES = {"complete", "needs_review", "blocked"}
_VALID_STATES = {
    "propose",
    "execute",
    "verify",
    "critique",
    "repair",
    "complete",
    "needs_review",
    "blocked",
}
_TRANSITIONS = {
    "propose": {"execute", "needs_review", "blocked"},
    "execute": {"verify", "repair", "needs_review", "blocked"},
    "verify": {"complete", "critique", "needs_review", "blocked"},
    "critique": {"repair", "needs_review", "blocked"},
    "repair": {"execute", "needs_review", "blocked"},
}


@dataclass(frozen=True)
class ImprovementTransitionDecision:
    allowed: bool
    from_state: str
    to_state: str
    reason: str
    terminal: bool
    max_loops_reached: bool


class ExecutionImprovementLoopService:
    def transition(
        self,
        *,
        from_state: str,
        to_state: str,
        attempt_index: int,
        max_loops: int,
    ) -> ImprovementTransitionDecision:
        f = str(from_state or "").strip().lower()
        t = str(to_state or "").strip().lower()
        if f not in _VALID_STATES or t not in _VALID_STATES:
            return ImprovementTransitionDecision(False, f, t, "invalid_state", False, False)
        if f in _TERMINAL_STATES:
            return ImprovementTransitionDecision(False, f, t, "terminal_state_no_transition", True, False)
        if t not in _TRANSITIONS.get(f, set()):
            return ImprovementTransitionDecision(False, f, t, "invalid_transition", t in _TERMINAL_STATES, False)
        loops = max(1, int(max_loops or 1))
        max_reached = int(attempt_index or 0) >= loops and t in {"repair", "execute", "verify", "critique"}
        if max_reached:
            return ImprovementTransitionDecision(False, f, t, "max_improvement_loops_reached", False, True)
        return ImprovementTransitionDecision(True, f, t, "ok", t in _TERMINAL_STATES, False)

    def build_verification_critique(
        self,
        *,
        expected_artifacts: list[dict[str, Any]] | None,
        verification: dict[str, Any] | None,
        observed_artifacts: list[dict[str, Any]] | None = None,
        logs: str | None = None,
    ) -> dict[str, Any]:
        expected = [dict(item) for item in list(expected_artifacts or []) if isinstance(item, dict)]
        observed = [dict(item) for item in list(observed_artifacts or []) if isinstance(item, dict)]
        verification_payload = dict(verification or {})
        reasons = list(verification_payload.get("failed_reasons") or [])
        if not reasons and verification_payload.get("reason"):
            reasons = [str(verification_payload.get("reason"))]
        observed_paths = {str(item.get("workspace_relative_path") or item.get("relative_path") or "").strip() for item in observed}
        expected_paths = {str(item.get("relative_path") or "").strip() for item in expected if str(item.get("relative_path") or "").strip()}
        missing_paths = sorted(path for path in expected_paths if path and path not in observed_paths)
        return {
            "schema": "verification_critique.v1",
            "status": "failed",
            "expected_artifacts_count": len(expected),
            "observed_artifacts_count": len(observed),
            "missing_paths": missing_paths,
            "failed_reasons": [str(item) for item in reasons],
            "logs_preview": str(logs or "")[:500],
            "repair_focus": "create_missing_artifacts_and_fix_verification_failures",
        }


_service = ExecutionImprovementLoopService()


def get_execution_improvement_loop_service() -> ExecutionImprovementLoopService:
    return _service

