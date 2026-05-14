from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptContextBundle:
    schema: str
    goal_id: str
    task_id: str
    task_kind: str
    contract_summary: dict[str, Any]
    context_summary: dict[str, Any]
    policy_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "task_kind": self.task_kind,
            "contract_summary": dict(self.contract_summary),
            "context_summary": dict(self.context_summary),
            "policy_summary": dict(self.policy_summary),
        }


class PromptContextBundleService:
    def build_for_propose_context(self, context) -> PromptContextBundle:
        task = context.task or {}
        worker_contract = dict(task.get("worker_execution_contract") or {})
        task_kind = str(task.get("task_kind") or "unknown")
        rc = context.research_context if isinstance(context.research_context, dict) else {}
        policy = getattr(context, "policy", None)
        return PromptContextBundle(
            schema="prompt_context_bundle.v1",
            goal_id=str(context.goal_id or ""),
            task_id=str(context.task_id or ""),
            task_kind=task_kind,
            contract_summary={
                "execution_mode": str(worker_contract.get("execution_mode") or ""),
                "strategy_mode": str(worker_contract.get("strategy_mode") or ""),
                "expected_artifacts_count": len(list(worker_contract.get("expected_artifacts") or [])),
                "allowed_tool_classes": list(worker_contract.get("allowed_tool_classes") or []),
            },
            context_summary={
                "research_context_present": bool(rc),
                "research_chunks": len(list(rc.get("chunks") or [])),
            },
            policy_summary={
                "allow_shell_execution": bool(getattr(policy, "allow_shell_execution", False)),
                "requires_executable_step": bool(getattr(policy, "requires_executable_step", False)),
            },
        )


_service = PromptContextBundleService()


def get_prompt_context_bundle_service() -> PromptContextBundleService:
    return _service

