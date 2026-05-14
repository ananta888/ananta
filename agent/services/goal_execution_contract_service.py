from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_SOFTWARE_KEYWORDS = ("software", "project", "java", "angular", "frontend", "backend", "fibonacci")


@dataclass(frozen=True)
class GoalExecutionContract:
    version: str
    execution_mode: str
    strategy_mode: str
    expected_artifacts: list[dict[str, Any]]
    allowed_tool_classes: list[str]
    verification_gates: list[dict[str, Any]]
    fallback_policy: dict[str, Any]
    audit_requirements: dict[str, Any]
    max_improvement_loops: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "goal_execution_contract.v1",
            "version": self.version,
            "execution_mode": self.execution_mode,
            "strategy_mode": self.strategy_mode,
            "expected_artifacts": list(self.expected_artifacts),
            "allowed_tool_classes": list(self.allowed_tool_classes),
            "verification_gates": list(self.verification_gates),
            "fallback_policy": dict(self.fallback_policy),
            "audit_requirements": dict(self.audit_requirements),
            "max_improvement_loops": int(self.max_improvement_loops),
            "source": self.source,
        }


class GoalExecutionContractService:
    def _default_expected_artifacts(self, *, goal_text: str, mode_data: dict[str, Any]) -> list[dict[str, Any]]:
        normalized = f"{goal_text} {mode_data}".lower()
        if any(token in normalized for token in _SOFTWARE_KEYWORDS):
            return [
                {"kind": "directory", "required": True, "relative_path": "backend", "description": "Backend project root"},
                {"kind": "directory", "required": True, "relative_path": "frontend", "description": "Frontend project root"},
            ]
        return []

    def default_contract(
        self,
        *,
        goal_text: str,
        execution_preferences: dict[str, Any] | None = None,
        mode_data: dict[str, Any] | None = None,
        source: str = "compatibility_adapter",
    ) -> GoalExecutionContract:
        execution_preferences = dict(execution_preferences or {})
        mode_data = dict(mode_data or {})
        strategy_mode = str(execution_preferences.get("strategy_mode") or mode_data.get("strategy_mode") or "openai_compatible_tool_calling").strip() or "openai_compatible_tool_calling"
        execution_mode = str(execution_preferences.get("execution_mode") or mode_data.get("execution_mode") or "llm_first_with_guardrails").strip() or "llm_first_with_guardrails"
        max_loops_raw = execution_preferences.get("max_improvement_loops", mode_data.get("max_improvement_loops", 3))
        try:
            max_loops = int(max_loops_raw)
        except (TypeError, ValueError):
            max_loops = 3
        max_loops = max(1, min(8, max_loops))
        expected_artifacts = self._default_expected_artifacts(goal_text=goal_text, mode_data=mode_data)
        return GoalExecutionContract(
            version="v1",
            execution_mode=execution_mode,
            strategy_mode=strategy_mode,
            expected_artifacts=expected_artifacts,
            allowed_tool_classes=["read", "write"],
            verification_gates=[
                {"id": "artifact_presence", "required": bool(expected_artifacts)},
            ],
            fallback_policy={
                "deterministic_allowed": True,
                "deterministic_only_as_explicit_or_final_fallback": True,
            },
            audit_requirements={
                "log_tool_remap": True,
                "log_policy_decisions": True,
                "log_fallback_transitions": True,
            },
            max_improvement_loops=max_loops,
            source=source,
        )

    def normalize_goal_contract(
        self,
        *,
        goal_text: str,
        execution_preferences: dict[str, Any] | None = None,
        mode_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        execution_preferences = dict(execution_preferences or {})
        raw = execution_preferences.get("goal_execution_contract")
        if isinstance(raw, dict) and str(raw.get("version") or "").strip():
            normalized = dict(raw)
            normalized.setdefault("schema", "goal_execution_contract.v1")
            normalized.setdefault("source", "goal_payload")
            return normalized
        return self.default_contract(
            goal_text=goal_text,
            execution_preferences=execution_preferences,
            mode_data=mode_data,
            source="compatibility_adapter",
        ).to_dict()

    def attach_to_execution_preferences(
        self,
        *,
        goal_text: str,
        execution_preferences: dict[str, Any] | None = None,
        mode_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(execution_preferences or {})
        payload["goal_execution_contract"] = self.normalize_goal_contract(
            goal_text=goal_text,
            execution_preferences=payload,
            mode_data=mode_data,
        )
        return payload

    def task_scoped_contract(
        self,
        *,
        goal_contract: dict[str, Any] | None,
        plan_id: str | None,
        plan_node_id: str | None,
        expected_artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        contract = dict(goal_contract or {})
        artifacts = [dict(a) for a in list(expected_artifacts or contract.get("expected_artifacts") or []) if isinstance(a, dict)]
        return {
            "schema": "worker_execution_contract.v1",
            "goal_contract_version": str(contract.get("version") or "v1"),
            "execution_mode": str(contract.get("execution_mode") or "llm_first_with_guardrails"),
            "strategy_mode": str(contract.get("strategy_mode") or "openai_compatible_tool_calling"),
            "allowed_tool_classes": list(contract.get("allowed_tool_classes") or ["read", "write"]),
            "expected_artifacts": artifacts,
            "verification_gates": list(contract.get("verification_gates") or []),
            "fallback_policy": dict(contract.get("fallback_policy") or {}),
            "max_improvement_loops": int(contract.get("max_improvement_loops") or 3),
            "traceability": {
                "plan_id": str(plan_id or "").strip() or None,
                "plan_node_id": str(plan_node_id or "").strip() or None,
            },
        }


goal_execution_contract_service = GoalExecutionContractService()


def get_goal_execution_contract_service() -> GoalExecutionContractService:
    return goal_execution_contract_service

