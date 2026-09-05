"""Hub-owned policy for bounded, auditable automatic approvals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

RECOVERY_MATERIALIZE_TOOL = "planning.recovery_plan.materialize"


class ApprovalAutoGrantPolicy:
    """Decide whether an exact approval request is pre-authorized."""

    def reason(
        self,
        *,
        policy_by_mode: Mapping[str, Any],
        human_required_tools: Sequence[str],
        tool_name: str,
        scope: Mapping[str, Any],
        governance_mode: str,
    ) -> str | None:
        name = str(tool_name or "").strip()
        if name in {str(value or "").strip() for value in human_required_tools}:
            return None

        mode = str(governance_mode or "balanced").strip() or "balanced"
        configured = policy_by_mode.get(mode)
        mode_policy = dict(configured) if isinstance(configured, Mapping) else {}
        approval_class = str(scope.get("approval_class") or "").strip()

        if approval_class == "read_only" and bool(mode_policy.get("read_only")):
            return "auto_approved:read_only"
        if approval_class == "controlled_workspace_writes" and bool(
            mode_policy.get("controlled_workspace_writes")
        ):
            return "auto_approved:controlled_workspace_writes"
        if name == "test.run" and bool(mode_policy.get("test_run")):
            return "auto_approved:test_run"
        if self._is_recovery_materialization(name=name, scope=scope) and bool(
            mode_policy.get("recovery_plan_materialization")
        ):
            return "auto_approved:recovery_plan_materialization"
        return None

    @staticmethod
    def _is_recovery_materialization(
        *,
        name: str,
        scope: Mapping[str, Any],
    ) -> bool:
        return (
            name == RECOVERY_MATERIALIZE_TOOL
            and str(scope.get("approval_class") or "").strip()
            == "task_materialization"
            and str(scope.get("source") or "").strip()
            == "model_context_recovery"
            and bool(str(scope.get("plan_id") or "").strip())
            and bool(str(scope.get("source_task_id") or "").strip())
            and bool(str(scope.get("recovery_key") or "").strip())
        )


__all__ = ["ApprovalAutoGrantPolicy", "RECOVERY_MATERIALIZE_TOOL"]
