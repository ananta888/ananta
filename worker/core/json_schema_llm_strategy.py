"""JsonSchemaLLMStrategy — FA-T009/T021/AFR-T004: response_format=json_object."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agent.services.model_invocation_service import LLMUnavailableError, ModelInvocationService
from ananta_contracts.model_recovery import metadata_from_llm_error
from agent.services.model_routing_contract import (
    ModelRoutingContractError,
    build_model_routing_context,
    model_routing_policy_failure_metadata,
)
from agent.services.prompt_context_bundle_service import get_prompt_context_bundle_service
from agent.services.propose_runtime_policy import resolve_propose_llm_timeout_seconds
from agent.services.strategy_prompt_composer import get_strategy_prompt_composer
from worker.core.propose import ExecutableProposal, ProposeStrategyResult
from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy

_MOCK_ONLY_PROVIDERS = {"mock"}

_SCHEMA_PROMPT_SUFFIX = """
Respond with valid JSON:
{"command": "...", "tool_calls": [], "reason": "..."}
or {"command": null, "tool_calls": [{"name": "...", "args": {...}}], "reason": "..."}
Only raw JSON. No prose. No markdown."""


class JsonSchemaLLMStrategy(ProposeStrategy):
    """Calls LLM with response_format=json_object, parses command/tool_calls."""

    JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "command": {"type": ["string", "null"]},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "args": {"type": "object"},
                    },
                },
            },
            "reason": {"type": "string"},
        },
        "anyOf": [
            {
                "required": ["command"],
                "properties": {"command": {"type": "string", "minLength": 1}},
            },
            {
                "required": ["tool_calls"],
                "properties": {"tool_calls": {"type": "array", "minItems": 1}},
            },
        ],
        "additionalProperties": False,
    }

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        from agent.config import settings

        _eff_cfg = dict(context.effective_config) if isinstance(context.effective_config, Mapping) else {}
        provider = (
            (str(_eff_cfg.get("default_provider") or "") or settings.default_provider or "lmstudio").strip().lower()
        )

        if provider in _MOCK_ONLY_PROVIDERS:
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason="provider_json_schema_not_supported_mock",
            )

        bundle = get_prompt_context_bundle_service().build_for_propose_context(context).to_dict()
        prompt = context.base_prompt + _SCHEMA_PROMPT_SUFFIX
        system_prompt = get_strategy_prompt_composer().compose_system_prompt(
            context=context,
            prompt_context_bundle=bundle,
            strategy_contract={
                "role": "You are a structured output generator.",
                "output_contract": (
                    "You MUST respond with valid JSON only — no prose, no markdown, no explanations.\n\n"
                    "The JSON must match this schema:\n"
                    '{\n  "command": "<shell command string, or null>",\n'
                    '  "tool_calls": [{"name": "<tool_name>", "args": {<arguments>}}]\n}\n\n'
                    'Rules:\n- Include at least one of "command" or "tool_calls".\n'
                    '- "reason" is optional but recommended.\n'
                    "- Output ONLY the raw JSON object. No fences. "
                    "No text before or after."
                ),
            },
        )

        try:
            timeout_seconds = resolve_propose_llm_timeout_seconds(
                effective_config=_eff_cfg,
                task_kind=str((context.task or {}).get("task_kind") or "").strip().lower() or None,
            )
            llm_result = ModelInvocationService.invoke_with_json_schema_result(
                prompt=prompt,
                json_schema=self.JSON_SCHEMA,
                model=None,
                system_prompt=system_prompt,
                timeout=timeout_seconds,
                retry_on_contract_error=True,
                routing_ctx=build_model_routing_context(
                    context.task,
                    context_text=context.base_prompt,
                    requires_json=True,
                ),
                provider_context=_eff_cfg.get("provider_context"),
                provider_contexts_by_profile_id=_eff_cfg.get("provider_contexts_by_profile_id"),
                provider_attempt_plan=_eff_cfg.get("provider_attempt_plan"),
            )
        except ModelRoutingContractError as exc:
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason="model_routing_policy_blocked",
                reason_codes=["model_routing_invalid", "policy_blocked"],
                metadata=model_routing_policy_failure_metadata(exc),
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable", "model_chain_exhausted"],
                metadata=metadata_from_llm_error(exc),
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "json_schema_llm",
                f"llm_call_failed: {exc}",
                metadata={
                    "llm_call_profile": [
                        {
                            "name": "propose_json_schema_llm",
                            "backend": "json_schema_llm",
                            "provider": provider,
                            "model": None,
                            "success": False,
                            "latency_ms": None,
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                            "source": "json_schema_llm_strategy",
                            "estimated": True,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "started_at": None,
                            "ended_at": None,
                        }
                    ]
                },
            )
        if isinstance(llm_result, str):
            raw_response = llm_result
            structured_output = None
            structured_output_valid = False
            structured_contract_present = False
            structured_output_issues = []
            llm_profile = []
            fallback_decisions = []
            llm_model = None
            llm_provider = provider
        elif isinstance(llm_result, Mapping):
            raw_response = str(llm_result.get("content") or "")
            structured_output = llm_result.get("structured_output")
            structured_output_valid = bool(llm_result.get("structured_output_valid", False))
            structured_contract_present = "structured_output_valid" in llm_result
            structured_output_issues = list(llm_result.get("structured_output_issues") or [])
            invocation_metadata = (
                dict(llm_result.get("metadata") or {}) if isinstance(llm_result.get("metadata"), Mapping) else {}
            )
            llm_profile = list(invocation_metadata.get("llm_call_profile") or [])
            fallback_decisions = [
                dict(item)
                for item in list(invocation_metadata.get("fallback_decisions") or [])
                if isinstance(item, Mapping)
            ]
            llm_model = str(llm_result.get("model") or "").strip() or None
            llm_provider = str(llm_result.get("provider") or "").strip() or provider
        else:
            raw_response = str(llm_result or "")
            structured_output = None
            structured_output_valid = False
            structured_contract_present = False
            structured_output_issues = []
            llm_profile = []
            fallback_decisions = []
            llm_model = None
            llm_provider = provider
        invocation_diagnostics = {
            **({"llm_call_profile": llm_profile} if llm_profile else {}),
            **({"fallback_decisions": fallback_decisions} if fallback_decisions else {}),
        }

        if not raw_response or not raw_response.strip():
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason="llm_returned_empty_response",
                metadata=invocation_diagnostics or None,
            )

        if structured_contract_present and not structured_output_valid:
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=raw_response[:300],
                reason="structured_output_validation_failed",
                reason_codes=["structured_output_validation_failed"],
                metadata={
                    **invocation_diagnostics,
                    "structured_output_issues": structured_output_issues,
                },
            )
        try:
            parsed = structured_output if structured_contract_present else json.loads(raw_response)
        except json.JSONDecodeError:
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=raw_response[:300],
                reason="json_parse_failed",
                reason_codes=["json_parse_failed"],
                metadata=invocation_diagnostics or None,
            )

        if not isinstance(parsed, dict):
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=str(parsed)[:300],
                reason="json_not_object",
                metadata=invocation_diagnostics or None,
            )

        tool_calls = parsed.get("tool_calls") or []
        command = parsed.get("command") or None
        if command:
            command = str(command).strip() or None

        # Validate tool calls
        valid_tcs = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("name")]

        if valid_tcs:
            proposal = ExecutableProposal(
                proposal_id=f"jsllm-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="json_schema_llm",
                command=None,
                tool_calls=valid_tcs,
                expected_artifacts=["workspace-changes"],
                metadata={
                    "provider": llm_provider,
                    "model": llm_model,
                    "llm_call_profile": llm_profile,
                    "fallback_decisions": fallback_decisions,
                    "prompt_context_bundle": {
                        "schema": bundle.get("schema"),
                        "task_kind": bundle.get("task_kind"),
                        "selected_chunks": ((bundle.get("context_summary") or {}).get("budget") or {}).get(
                            "selected_count"
                        ),
                        "instruction_layers_present": bool(
                            (bundle.get("context_summary") or {}).get("instruction_layers_present")
                        ),
                        "instruction_stack_present": bool(
                            (bundle.get("context_summary") or {}).get("instruction_stack_present")
                        ),
                        "instruction_stack_checksum": (bundle.get("context_summary") or {}).get(
                            "instruction_stack_checksum"
                        ),
                    },
                },
            )
            return ProposeStrategyResult.executable(
                "json_schema_llm",
                proposal,
                metadata=invocation_diagnostics,
            )

        if command:
            proposal = ExecutableProposal(
                proposal_id=f"jsllm-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="json_schema_llm",
                command=command,
                tool_calls=[],
                expected_artifacts=["command_output"],
                metadata={
                    "provider": llm_provider,
                    "model": llm_model,
                    "llm_call_profile": llm_profile,
                    "fallback_decisions": fallback_decisions,
                    "prompt_context_bundle": {
                        "schema": bundle.get("schema"),
                        "task_kind": bundle.get("task_kind"),
                        "selected_chunks": ((bundle.get("context_summary") or {}).get("budget") or {}).get(
                            "selected_count"
                        ),
                        "instruction_layers_present": bool(
                            (bundle.get("context_summary") or {}).get("instruction_layers_present")
                        ),
                        "instruction_stack_present": bool(
                            (bundle.get("context_summary") or {}).get("instruction_stack_present")
                        ),
                        "instruction_stack_checksum": (bundle.get("context_summary") or {}).get(
                            "instruction_stack_checksum"
                        ),
                    },
                },
            )
            return ProposeStrategyResult.executable(
                "json_schema_llm",
                proposal,
                metadata=invocation_diagnostics,
            )

        return ProposeStrategyResult.declined(
            "json_schema_llm",
            reason="llm_returned_no_executable_output",
            metadata=invocation_diagnostics or None,
        )
