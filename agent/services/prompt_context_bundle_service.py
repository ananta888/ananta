from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.common.redaction import redact


_SENSITIVE_CLASSES = {"internal_high", "secret", "credential", "security_sensitive"}


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
    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _bounded_chunks(self, chunks: list[dict[str, Any]], *, max_chunks: int, total_budget_tokens: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        denied = 0
        truncated_for_budget = 0
        budget_used = 0
        for chunk in chunks:
            if len(selected) >= max_chunks:
                break
            meta = dict(chunk.get("metadata") or {})
            sensitivity = str(meta.get("sensitivity") or "public").strip().lower()
            if sensitivity in _SENSITIVE_CLASSES:
                denied += 1
                continue
            content = str(chunk.get("content") or "")
            token_estimate = self._safe_int(chunk.get("token_estimate"), max(1, len(content) // 4))
            if budget_used + token_estimate > total_budget_tokens:
                truncated_for_budget += 1
                continue
            budget_used += token_estimate
            selected.append(
                {
                    "source_id": str(chunk.get("source_id") or ""),
                    "title": str(chunk.get("title") or ""),
                    "token_estimate": token_estimate,
                    "metadata": redact(meta),
                }
            )
        return selected, {
            "input_count": len(chunks),
            "selected_count": len(selected),
            "denied_count": denied,
            "truncated_for_budget": truncated_for_budget,
            "budget_used_tokens": budget_used,
            "total_budget_tokens": total_budget_tokens,
        }

    def build_for_propose_context(self, context) -> PromptContextBundle:
        task = context.task or {}
        worker_contract = dict(task.get("worker_execution_contract") or {})
        task_kind = str(task.get("task_kind") or "unknown")
        rc = context.research_context if isinstance(context.research_context, dict) else {}
        chunks = [dict(item) for item in list(rc.get("chunks") or []) if isinstance(item, dict)]
        compact_chunks, bundle_budget = self._bounded_chunks(
            chunks,
            max_chunks=6,
            total_budget_tokens=8000,
        )
        worker_context = dict(task.get("worker_execution_context") or {})
        instruction_layers = dict(worker_context.get("instruction_layers") or {})
        instruction_context = dict(worker_context.get("instruction_context") or {})
        instruction_stack = dict(getattr(context, "instruction_stack", None) or {})
        instruction_diagnostics = dict(getattr(context, "instruction_diagnostics", None) or {})
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
                "verification_gates_count": len(list(worker_contract.get("verification_gates") or [])),
            },
            context_summary={
                "research_context_present": bool(rc),
                "research_chunks": len(list(rc.get("chunks") or [])),
                "selected_chunks": compact_chunks,
                "budget": bundle_budget,
                "instruction_layers_present": bool(instruction_layers),
                "instruction_layers": redact(instruction_layers),
                "instruction_stack_present": bool(instruction_stack),
                "instruction_stack_checksum": str(instruction_stack.get("checksum") or "").strip() or None,
                "instruction_stack_summary": {
                    "applied_layers": [str((item or {}).get("layer") or "") for item in list(instruction_stack.get("applied_layers") or [])],
                    "suppressed_layers": [str((item or {}).get("layer") or "") for item in list(instruction_stack.get("suppressed_layers") or [])],
                    "role_template_context": redact(dict(instruction_stack.get("role_template_context") or {})),
                }
                if instruction_stack
                else None,
                "instruction_diagnostics": redact(instruction_diagnostics) if instruction_diagnostics else None,
                "instruction_selection": {
                    "owner_username": str(instruction_context.get("owner_username") or ""),
                    "profile_id": str(instruction_context.get("profile_id") or ""),
                    "overlay_id": str(instruction_context.get("overlay_id") or ""),
                    "selection_reason": "goal_execution_preferences_instruction_context",
                },
            },
            policy_summary={
                "allow_shell_execution": bool(getattr(policy, "allow_shell_execution", False)),
                "requires_executable_step": bool(getattr(policy, "requires_executable_step", False)),
            },
        )


_service = PromptContextBundleService()


def get_prompt_context_bundle_service() -> PromptContextBundleService:
    return _service
