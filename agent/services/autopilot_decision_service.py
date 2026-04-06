from __future__ import annotations

import json
from typing import Any

from agent.common.utils.structured_action_utils import normalize_structured_action_payload, parse_structured_action_payload
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.services.verification_policy_service import evaluate_quality_gates
from agent.utils import _extract_reason


class AutopilotDecisionService:
    """Pure helper logic for proposal snapshots, tool guardrails, and execution result normalization."""

    def normalize_proposal_data(self, propose_data: dict | None) -> dict[str, Any]:
        normalized: dict[str, Any] = dict(propose_data or {})
        if normalized.get("command") or normalized.get("tool_calls"):
            payload = normalize_structured_action_payload(
                {
                    "reason": normalized.get("reason"),
                    "command": normalized.get("command"),
                    "tool_calls": normalized.get("tool_calls"),
                }
            )
            if payload:
                normalized["command"] = payload.get("command")
                normalized["tool_calls"] = payload.get("tool_calls")
                if payload.get("reason") and not str(normalized.get("reason") or "").strip():
                    normalized["reason"] = payload.get("reason")
            return normalized
        raw = normalized.get("raw")
        if raw is None:
            return normalized
        payload = parse_structured_action_payload(str(raw))
        if payload:
            normalized["command"] = payload.get("command")
            normalized["tool_calls"] = payload.get("tool_calls")
            if payload.get("reason") and (not normalized.get("reason") or str(normalized.get("reason")).strip() == str(raw).strip()):
                normalized["reason"] = payload.get("reason")
            return normalized
        reason = _extract_reason(str(raw))
        if reason and (not normalized.get("reason") or str(normalized.get("reason")).strip() == str(raw).strip()):
            normalized["reason"] = reason
        return normalized

    def build_proposal_snapshot(self, propose_data: dict) -> dict[str, Any]:
        normalized = self.normalize_proposal_data(propose_data)
        reason = normalized.get("reason")
        command = normalized.get("command")
        tool_calls = normalized.get("tool_calls")
        raw = normalized.get("raw")
        raw_preview = str(raw or "")[:280] if raw is not None else None
        snapshot: dict[str, Any] = {
            "reason": reason,
            "command": command,
            "tool_calls": tool_calls,
            "raw_preview": raw_preview,
        }
        if normalized.get("backend") is not None:
            snapshot["backend"] = normalized.get("backend")
        if isinstance(normalized.get("routing"), dict):
            snapshot["routing"] = normalized.get("routing")
        if isinstance(normalized.get("cli_result"), dict):
            snapshot["cli_result"] = normalized.get("cli_result")
        return snapshot

    def evaluate_tool_guardrails_for_autopilot(
        self,
        *,
        task: Any,
        policy: dict[str, Any],
        agent_cfg: dict[str, Any],
        reason: str | None,
        command: str | None,
        tool_calls: list | None,
    ):
        if not tool_calls:
            return None
        dynamic_guard = dict((agent_cfg.get("llm_tool_guardrails", {}) or {}))
        tool_classes = dynamic_guard.get("tool_classes", {}) or {}
        allowed_classes = set(policy["allowed_tool_classes"])
        all_classes = set(tool_classes.values()) | {"unknown"}
        dynamic_guard["blocked_classes"] = sorted([item for item in all_classes if item not in allowed_classes])
        token_usage = {
            "prompt_tokens": estimate_text_tokens(command or reason or getattr(task, "description", None)),
            "history_tokens": estimate_text_tokens(json.dumps(getattr(task, "history", None) or [], ensure_ascii=False)),
            "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
        }
        token_usage["estimated_total_tokens"] = sum(int(token_usage.get(k) or 0) for k in token_usage)
        return evaluate_tool_call_guardrails(tool_calls, {"llm_tool_guardrails": dynamic_guard}, token_usage=token_usage)

    def normalize_execute_result(self, execute_data: dict) -> tuple[str, int | None, str | None]:
        exit_code = execute_data.get("exit_code")
        output = execute_data.get("output")
        task_status = execute_data.get("status")
        if task_status not in {"completed", "failed"}:
            task_status = "completed" if (exit_code in (None, 0)) else "failed"
        return task_status, exit_code, output

    def apply_quality_gate_if_needed(
        self,
        *,
        task: Any,
        task_status: str,
        output: str | None,
        exit_code: int | None,
        agent_cfg: dict[str, Any],
    ) -> tuple[str, str | None, str | None]:
        if task_status != "completed":
            return task_status, output, None
        quality_cfg = agent_cfg.get("quality_gates", {})
        if not quality_cfg.get("autopilot_enforce", True):
            return task_status, output, None
        passed, reason_code = evaluate_quality_gates(task, output, exit_code, policy=quality_cfg)
        if passed:
            return task_status, output, None
        failed_output = f"{output}\n\n[quality_gate] failed: {reason_code}" if output else f"[quality_gate] failed: {reason_code}"
        return "failed", failed_output, reason_code


autopilot_decision_service = AutopilotDecisionService()


def get_autopilot_decision_service() -> AutopilotDecisionService:
    return autopilot_decision_service
