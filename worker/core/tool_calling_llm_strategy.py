"""ToolCallingLLMStrategy — FA-T009/T021/AFR-T004: real API tools= param."""
from __future__ import annotations

import json

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, ExecutableProposal
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError
from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.context_bundle_service import ContextBundler
from agent.services.prompt_context_bundle_service import get_prompt_context_bundle_service

_MOCK_ONLY_PROVIDERS = {"mock"}


def _build_system_prompt(context: ProposeContext) -> str:
    task = context.task or {}
    task_desc = (task.get("description") or task.get("prompt") or "").strip()
    parts = [
        "You are a software engineering agent executing a task.",
        f"Goal: {context.goal_id}",
        f"Task: {context.task_id}",
        f"Task kind: {task.get('task_kind') or 'unknown'}",
    ]
    if task_desc and len(task_desc) > 20:
        parts.append("")
        parts.append("Task description:")
        parts.append(task_desc)
    rc = context.research_context if isinstance(context.research_context, dict) else {}
    if rc:
        bundle = ContextBundler.build_bundle(
            query=task_desc or context.base_prompt,
            context_payload=rc,
            policy_mode="standard",
            llm_scope="external_cloud_allowed",
        )
        parts.append("")
        parts.append("Governed context summary:")
        parts.append(
            f"chunks={bundle.get('chunk_count', 0)} denied={((bundle.get('policy_filter') or {}).get('denied_count', 0))}"
        )
    pcb = get_prompt_context_bundle_service().build_for_propose_context(context).to_dict()
    parts.append("")
    parts.append("Prompt context bundle:")
    parts.append(json.dumps(pcb, ensure_ascii=True, sort_keys=True))
    parts.append("")
    parts.append(
        "You MUST use one or more of the available tools to complete the task. "
        "Return ONLY tool_calls — no prose, no explanations, no markdown. "
        "Each tool_call must specify the function name and its arguments."
    )
    return "\n".join(parts)


class ToolCallingLLMStrategy(ProposeStrategy):
    """Calls a real OpenAI-compatible endpoint with tools= parameter."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        from agent.config import settings
        provider = (settings.default_provider or "lmstudio").strip().lower()
        pcb = get_prompt_context_bundle_service().build_for_propose_context(context).to_dict()

        if provider in _MOCK_ONLY_PROVIDERS:
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="provider_tools_not_supported_mock",
            )

        resolver = context.tool_definitions_resolver
        tools = resolver() if resolver is not None else []
        if not tools:
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="no_tools_defined",
            )
        allowed_tool_names: set[str] = set()
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            direct_name = str(tool.get("name") or "").strip()
            if direct_name:
                allowed_tool_names.add(direct_name)
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            fn_name = str(fn.get("name") or "").strip()
            if fn_name:
                allowed_tool_names.add(fn_name)

        try:
            llm_response = ModelInvocationService.invoke_with_tools(
                prompt=context.base_prompt,
                tools=tools,
                model=None,
                system_prompt=_build_system_prompt(context),
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "tool_calling_llm",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable"],
                metadata={"llm_call_profile": list(getattr(exc, "llm_call_profile", []) or [])},
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "tool_calling_llm", f"llm_call_failed: {exc}",
                metadata={
                    "llm_call_profile": [
                        {
                            "name": "propose_tool_calling_llm",
                            "backend": "tool_calling_llm",
                            "provider": provider,
                            "model": None,
                            "success": False,
                            "latency_ms": None,
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                            "source": "tool_calling_llm_strategy",
                            "estimated": True,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "started_at": None,
                            "ended_at": None,
                        }
                    ]
                },
            )

        tool_calls = llm_response.get("tool_calls") or []
        content = llm_response.get("content") or ""
        finish_reason = llm_response.get("finish_reason") or ""
        allow_shell_execution = bool(
            getattr(getattr(context, "policy", None), "allow_shell_execution", False)
        )

        # No native tool calls → try to extract from content via normalizer
        if not tool_calls and content.strip():
            normalizer = LLMResponseNormalizer()
            fallback = normalizer.normalize(
                content,
                context,
                allow_shell_execution=allow_shell_execution,
            )
            if isinstance(fallback.metadata, dict):
                fallback.metadata["source"] = "tool_calling_llm_content_fallback"
                fallback.metadata["allow_shell_execution"] = allow_shell_execution
            if fallback.is_executable or fallback.status == "advisory":
                return fallback

        if not tool_calls:
            if finish_reason in ("stop", "length"):
                return ProposeStrategyResult.declined(
                    "tool_calling_llm",
                    reason="tools_not_supported_model_returned_stop",
                    reason_codes=["tools_not_supported"],
                )
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="llm_returned_no_tool_calls",
            )

        # Validate tool calls: each must have a name
        valid_tcs = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_name = str(tc.get("name") or "").strip()
            if not tc_name:
                continue
            if allowed_tool_names and tc_name not in allowed_tool_names:
                continue
            valid_tcs.append(tc)
        if not valid_tcs:
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="tool_calls_invalid_or_missing_names",
            )

        proposal = ExecutableProposal(
            proposal_id=f"tcllm-{context.task_id}",
            goal_id=context.goal_id,
            task_id=context.task_id,
            strategy_id="tool_calling_llm",
            command=None,
            tool_calls=valid_tcs,
            expected_artifacts=["workspace-changes"],
            metadata={
                "provider": str(llm_response.get("provider") or provider).strip().lower() or provider,
                "model": str(llm_response.get("model") or "").strip() or None,
                "llm_call_profile": list(((llm_response.get("metadata") or {}).get("llm_call_profile") or [])),
                "tools_used": [tc.get("name") for tc in valid_tcs],
                "prompt_context_bundle": {
                    "schema": pcb.get("schema"),
                    "task_kind": pcb.get("task_kind"),
                    "selected_chunks": ((pcb.get("context_summary") or {}).get("budget") or {}).get("selected_count"),
                    "instruction_layers_present": bool((pcb.get("context_summary") or {}).get("instruction_layers_present")),
                },
            },
        )
        return ProposeStrategyResult.executable("tool_calling_llm", proposal)
