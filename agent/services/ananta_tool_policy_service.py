"""AWTCL-006: Policy gate for ananta-worker tool requests.

Every tool_request the worker LLM emits passes through this gate before
the hub executes anything. The gate decides one of:

- ``allow``              — the hub may execute the tool now.
- ``approval_required``  — the tool is legitimate but needs a separate
                           hub-side approval before execution.
- ``policy_blocked``     — the tool is unknown, blocked, denied for the
                           backend, or outside the current mutation mode.

The gate never executes anything itself and never auto-grants approvals;
the hub stays the final decider (AWTCL-DD-001). Hermes denied
capabilities and the propose-only rule for OpenCode/Aider/Codex from
``ToolRoutingService`` are enforced here so the worker cannot bypass
them (AWTCL-006 acceptance criteria).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.ananta_tool_registry_service import (
    CATEGORY_BLOCKED,
    CATEGORY_CONTROLLED_EXECUTION,
    CATEGORY_CONTROLLED_WRITE,
    CATEGORY_READ_ONLY,
    get_ananta_tool_registry_service,
)

DECISION_ALLOW = "allow"
DECISION_APPROVAL_REQUIRED = "approval_required"
DECISION_POLICY_BLOCKED = "policy_blocked"

# Mirrors the hermes capability rules in ToolRoutingService (HF-T002/HF-T003):
# planning/review/summarize/patch_propose/research_limited only.
_HERMES_ALLOWED_TOOLS = {"hermes.review"}
_EXTERNAL_PROPOSE_TOOLS = {"opencode.propose", "aider.propose", "codex.propose", "hermes.review"}


@dataclass(frozen=True)
class ToolPolicyDecision:
    decision: str
    reason: str
    rule_id: str
    risk_class: str = "unknown"
    tool_name: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == DECISION_ALLOW

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "risk_class": self.risk_class,
            "tool_name": self.tool_name,
            "policy_version": "ananta-tool-policy-v1",
        }


class AnantaToolPolicyService:
    """Evaluates tool requests against registry, scope and mutation mode."""

    def evaluate(
        self,
        *,
        tool_name: str | None,
        arguments: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        mutation_mode: str = "read_only",
        task_id: str | None = None,
        goal_id: str | None = None,
    ) -> ToolPolicyDecision:
        name = str(tool_name or "").strip()
        registry = get_ananta_tool_registry_service()
        spec = registry.get_tool(name)
        if spec is None:
            return ToolPolicyDecision(
                decision=DECISION_POLICY_BLOCKED,
                reason=f"unknown_tool:{name or 'empty'}",
                rule_id="unknown_tool_rejected",
                tool_name=name,
            )

        if spec.category == CATEGORY_BLOCKED:
            # Blocked tools never run via the worker loop. Even an approval
            # token only documents the request; execution stays hub-manual.
            return ToolPolicyDecision(
                decision=DECISION_POLICY_BLOCKED,
                reason="blocked_without_separate_approval",
                rule_id="blocked_category",
                risk_class=spec.risk_class,
                tool_name=name,
            )

        scope = {str(item or "").strip() for item in (allowed_tools or []) if str(item or "").strip()}
        if scope and name not in scope:
            return ToolPolicyDecision(
                decision=DECISION_POLICY_BLOCKED,
                reason="tool_not_in_allowed_scope",
                rule_id="allowed_tools_scope",
                risk_class=spec.risk_class,
                tool_name=name,
            )

        if name.startswith("hermes.") and name not in _HERMES_ALLOWED_TOOLS:
            return ToolPolicyDecision(
                decision=DECISION_POLICY_BLOCKED,
                reason="hermes_capability_denied",
                rule_id="hermes_denied_capabilities",
                risk_class=spec.risk_class,
                tool_name=name,
            )

        if spec.category == CATEGORY_READ_ONLY:
            return ToolPolicyDecision(
                decision=DECISION_ALLOW,
                reason="read_only_in_scope",
                rule_id="read_only_allowed",
                risk_class=spec.risk_class,
                tool_name=name,
            )

        if spec.category == CATEGORY_CONTROLLED_WRITE:
            modes = set(spec.policy_requirements.get("allowed_mutation_modes") or [])
            mode = str(mutation_mode or "read_only").strip().lower()
            if mode == "read_only" or (modes and mode not in modes):
                return ToolPolicyDecision(
                    decision=DECISION_POLICY_BLOCKED,
                    reason=f"write_tool_not_allowed_in_mode:{mode}",
                    rule_id="mutation_mode_gate",
                    risk_class=spec.risk_class,
                    tool_name=name,
                )
            if spec.policy_requirements.get("requires_approval") and not self._has_request_grant(
                tool_name=name, arguments=arguments, task_id=task_id, goal_id=goal_id
            ):
                return ToolPolicyDecision(
                    decision=DECISION_APPROVAL_REQUIRED,
                    reason="write_tool_requires_hub_approval",
                    rule_id="write_approval_gate",
                    risk_class=spec.risk_class,
                    tool_name=name,
                )
            return ToolPolicyDecision(
                decision=DECISION_ALLOW,
                reason=f"write_tool_allowed_in_mode:{mode}",
                rule_id="mutation_mode_gate",
                risk_class=spec.risk_class,
                tool_name=name,
            )

        if spec.category == CATEGORY_CONTROLLED_EXECUTION:
            if name in _EXTERNAL_PROPOSE_TOOLS:
                # Propose/review only: execution happens, mutation does not.
                # OpenCode/Aider/Codex mutations stay behind hub approval
                # (external_worker.execute_mutation is blocked).
                return ToolPolicyDecision(
                    decision=DECISION_ALLOW,
                    reason="external_backend_propose_only",
                    rule_id="external_propose_gate",
                    risk_class=spec.risk_class,
                    tool_name=name,
                )
            if spec.policy_requirements.get("requires_approval") and not self._has_request_grant(
                tool_name=name, arguments=arguments, task_id=task_id, goal_id=goal_id
            ):
                return ToolPolicyDecision(
                    decision=DECISION_APPROVAL_REQUIRED,
                    reason="execution_tool_requires_hub_approval",
                    rule_id="execution_approval_gate",
                    risk_class=spec.risk_class,
                    tool_name=name,
                )
            return ToolPolicyDecision(
                decision=DECISION_ALLOW,
                reason="allowlisted_execution_tool",
                rule_id="execution_allowlist_gate",
                risk_class=spec.risk_class,
                tool_name=name,
            )

        return ToolPolicyDecision(
            decision=DECISION_POLICY_BLOCKED,
            reason=f"unhandled_tool_category:{spec.category}",
            rule_id="category_fallback_block",
            risk_class=spec.risk_class,
            tool_name=name,
        )

    @staticmethod
    def _has_request_grant(
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        task_id: str | None,
        goal_id: str | None,
    ) -> bool:
        """ALWA-005/ALWA-FIND-007: digest-bound grant resolution.

        Replaces the former tool-name-based approvals list: a grant counts
        only when a persisted ApprovalRequest with status=granted matches
        the exact arguments_digest of this call (or a goal-scoped
        pre-approval covers the tool). Without DB access (worker context)
        this resolves to False — never to a silent allow.
        """
        try:
            from agent.services.approval_request_service import get_approval_request_service

            svc = get_approval_request_service()
            grant = svc.resolve_grant_for_call(
                tool_name=tool_name, arguments=arguments, task_id=task_id, goal_id=goal_id
            )
            if grant is not None:
                return True
            return svc.resolve_goal_pre_approval(goal_id=goal_id, tool_name=tool_name) is not None
        except Exception:
            return False


ananta_tool_policy_service = AnantaToolPolicyService()


def get_ananta_tool_policy_service() -> AnantaToolPolicyService:
    return ananta_tool_policy_service
