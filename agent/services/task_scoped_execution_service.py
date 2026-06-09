from __future__ import annotations

import concurrent.futures
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from flask import current_app, g, has_app_context, has_request_context

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import TaskConflictError, TaskNotFoundError, WorkerForwardingError
from agent.common.sgpt import SUPPORTED_CLI_BACKENDS, resolve_codex_runtime_config
from agent.common.utils.structured_action_utils import (
    extract_structured_action_fields,
    locally_repair_structured_action_output,
    normalize_structured_action_payload,
    parse_structured_action_payload,
    sanitize_structured_output_text,
)
from agent.config import settings
from agent.model_selection import normalize_legacy_model_name
from agent.models import TaskStepExecuteRequest
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import is_research_backend, normalize_research_artifact
from agent.routes.tasks.orchestration_policy import derive_required_capabilities, derive_research_specialization
from agent.runtime_policy import (
    build_trace_record,
    normalize_task_kind,
    resolve_cli_backend,
    review_policy,
    runtime_routing_config,
)
from agent.security_risk import (
    classify_command_risk,
    classify_tool_calls_risk,
    has_file_access_signal,
    has_terminal_signal,
    max_risk_level,
)
from agent.services.cli_session_service import get_cli_session_service
from agent.services.context_manager_service import get_context_manager_service
from agent.services.live_terminal_session_service import get_live_terminal_session_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.bridge_adapter_registry import BridgeAdapterRegistry
from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_loader import DomainPolicyLoader
from agent.services.domain_policy_service import DomainPolicyService
from agent.services.domain_registry import DomainRegistry
from agent.services.native_worker_runtime_service import get_native_worker_runtime_service
from agent.services.repository_registry import get_repository_registry
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.research_context_bridge_service import get_research_context_bridge_service
from agent.services.service_registry import get_core_services
from agent.services.task_execution_service import LocalExecutionResult
from agent.services.task_execution_policy_service import normalize_allowed_tools, resolve_execution_policy
from agent.services.task_handler_registry import get_task_handler_registry
from agent.services.execution_improvement_loop_service import get_execution_improvement_loop_service
from agent.services.planning_context_compactor_service import get_planning_context_compactor_service
from agent.services.product_event_service import record_product_event
from agent.services.worker_execution_profile_service import (
    normalize_worker_execution_profile,
    resolve_worker_execution_profile,
)
from agent.services.task_runtime_service import (
    apply_artifact_first_completion,
    get_local_task_status,
    update_local_task_status,
)
from agent.services.task_template_resolution import resolve_task_role_template
from agent.services.verification_service import get_verification_service
from agent.llm_integration import build_llm_call_profile_entry, normalize_llm_call_profile_entry
from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.services.propose_policy_service import get_propose_policy_service
from agent.services.propose_policy import get_task_kind_preset
from agent.utils import _extract_reason, _log_terminal_entry
from worker.core.propose import ExecutableProposal

_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def _build_workspace_state_sync_record(
    *,
    task: dict,
    materialization_manifest: object,
    workspace_artifact_refs: list,
    git_pushed: bool,
) -> dict:
    # Implementation lives in agent.services._task_scoped_workspace_sync.
    # Re-exported here for backward compat (12-month deprecation window, see
    # SPLIT-001 in todos/todo.refactor-large-files-split.json).
    from agent.services._task_scoped_workspace_sync import (
        build_workspace_state_sync_record as _impl,
    )
    return _impl(
        task=task,
        materialization_manifest=materialization_manifest,
        workspace_artifact_refs=workspace_artifact_refs,
        git_pushed=git_pushed,
    )


def build_hermes_context_blocks(
    *,
    task: dict,
    request_data: object,
    research_context: object,
) -> list:
    # SPLIT-001k: implementation lives in agent.services._task_scoped_hermes_context.
    # Re-exported here for backward compat (12-month deprecation window).
    from agent.services._task_scoped_hermes_context import (
        build_hermes_context_blocks as _impl,
    )
    return _impl(task=task, request_data=request_data, research_context=research_context)


@dataclass(frozen=True)
class TaskScopedRouteResponse:
    data: dict
    status: str = "success"
    message: str | None = None
    code: int = 200


class TaskScopedExecutionService:
    """Owns task-scoped proposal/execution orchestration so routes stay thin.

    .. note::

        The class is being progressively split (SPLIT-001) into focused
        helper modules under ``agent.services._task_scoped_*`` plus this
        thin orchestrator. The method groups below are the SRP clusters
        the split targets. Do not add new logic into a method that belongs
        to a different cluster; open a new helper module instead.

        Cluster map (matches todos/todo.refactor-large-files-split.json):

        ====================  ==============  =================================
        Cluster               Methods (n)     Helper module
        ====================  ==============  =================================
        config_policy         21              _task_scoped_config_policy
        workspace_runtime     1               _task_scoped_workspace_sync
        domain_action         5               _task_scoped_domain_actions
        forwarding_hub        5               _task_scoped_forwarding
        adapters (hermes/     5               _task_scoped_adapters
          handler)
        cli_session (native   12              _task_scoped_runtime
          opencode / research)
        repair (structured    14              _task_scoped_repair
          action)
        citation / grounded   7               _task_scoped_citation
          answer
        propose + execute     2 public        this class
        task_aux              5               this class
        ====================  ==============  =================================
    """

    @staticmethod
    # --- cluster: config_policy (resolvers, bounded_*, normalize) ---
    def _allow_synthetic_llm_profile_fallback() -> bool:
        if not has_app_context():
            return False
        cfg = (current_app.config.get("AGENT_CONFIG", {}) or {})
        policy = dict(cfg.get("llm_profile_policy") or {})
        return bool(policy.get("allow_synthetic_fallback", False))

    @staticmethod
    def _is_interactive_terminal_session(session_payload: dict | None) -> bool:
        metadata = (session_payload or {}).get("metadata") if isinstance((session_payload or {}).get("metadata"), dict) else {}
        return str(metadata.get("opencode_execution_mode") or "").strip().lower() == "interactive_terminal"

    @staticmethod
    def _normalize_temperature(value: float | int | str | None) -> float | None:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services._task_scoped_config_policy.normalize_temperature.
        from agent.services._task_scoped_config_policy import normalize_temperature
        return normalize_temperature(value)

    @staticmethod
    def _default_model(agent_cfg: dict) -> str | None:
        provider = str(agent_cfg.get("default_provider") or agent_cfg.get("provider") or "").strip().lower() or None
        return normalize_legacy_model_name(
            str(agent_cfg.get("default_model") or agent_cfg.get("model") or "").strip() or None,
            provider=provider,
        )

    @classmethod
    def _resolve_requested_model(cls, *, agent_cfg: dict, requested_model: str | None) -> str | None:
        provider = str(agent_cfg.get("default_provider") or agent_cfg.get("provider") or "").strip().lower() or None
        resolved = str(requested_model or "").strip() or cls._default_model(agent_cfg)
        return normalize_legacy_model_name(resolved, provider=provider)

    @staticmethod
    def _resolve_task_propose_timeout(agent_cfg: dict, task_kind: str) -> int:
        task_kind_policies = agent_cfg.get("task_kind_execution_policies") if isinstance(agent_cfg.get("task_kind_execution_policies"), dict) else {}
        task_kind_cfg = task_kind_policies.get(task_kind) if isinstance(task_kind_policies.get(task_kind), dict) else {}
        general_timeout = int(agent_cfg.get("command_timeout", 60) or 60)
        kind_timeout = int(task_kind_cfg.get("command_timeout") or 0)
        proposal_timeout = int(agent_cfg.get("task_propose_timeout_seconds") or 0)
        return max(60, general_timeout, kind_timeout, proposal_timeout)

    @staticmethod
    def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services._task_scoped_config_policy.bounded_int.
        from agent.services._task_scoped_config_policy import bounded_int
        return bounded_int(value, default=default, minimum=minimum, maximum=maximum)

    @staticmethod
    def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services._task_scoped_config_policy.bounded_float.
        from agent.services._task_scoped_config_policy import bounded_float
        return bounded_float(value, default=default, minimum=minimum, maximum=maximum)

    @staticmethod
    # --- cluster: workspace_runtime (command rewrite) ---
    def _rewrite_runtime_command_for_workspace_tools(*, command: str | None, workspace_dir: str | None) -> tuple[str | None, dict | None]:
        command_text = str(command or "").strip()
        workspace = str(workspace_dir or "").strip()
        if not command_text or not workspace:
            return command, None
        if "uvicorn" not in command_text:
            return command, None

        venv_uvicorn = Path(workspace) / ".venv" / "bin" / "uvicorn"
        if venv_uvicorn.exists():
            # Replace bare uvicorn token only, keep shell operators/arguments unchanged.
            rewritten = re.sub(r"(?<![\\w./-])uvicorn(?![\\w./-])", str(venv_uvicorn), command_text)
            if rewritten != command_text:
                return rewritten, {
                    "strategy": "workspace_venv_uvicorn_binary",
                    "from": "uvicorn",
                    "to": str(venv_uvicorn),
                }

        venv_activate = Path(workspace) / ".venv" / "bin" / "activate"
        if venv_activate.exists() and ".venv/bin/activate" not in command_text:
            rewritten = f"source .venv/bin/activate && {command_text}"
            return rewritten, {
                "strategy": "workspace_venv_activate_prefix",
                "activate_script": ".venv/bin/activate",
            }
        return command, None

    @classmethod
    def _resolve_worker_semantic_output_correction_policy(cls, agent_cfg: dict | None) -> dict:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_worker_semantic_output_correction_policy
        from agent.services._task_scoped_config_policy import (
            resolve_worker_semantic_output_correction_policy,
        )
        return resolve_worker_semantic_output_correction_policy(agent_cfg)

    @classmethod
    def _resolve_interactive_context_profile(cls, agent_cfg: dict | None, *, retry: bool = False) -> dict:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_interactive_context_profile
        from agent.services._task_scoped_config_policy import (
            resolve_interactive_context_profile,
        )
        return resolve_interactive_context_profile(agent_cfg, retry=retry)

    @classmethod
    def _compact_research_context(
        cls,
        research_context: dict | None,
        *,
        profile: dict | None,
    ) -> dict | None:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.compact_research_context
        from agent.services._task_scoped_config_policy import compact_research_context
        return compact_research_context(research_context, profile=profile)

    @staticmethod
    def _interactive_timeout_like_failure(*, rc: int, output: str, stderr: str) -> bool:
        text = f"{output or ''}\n{stderr or ''}".strip()
        if rc != 0 and not text:
            return True
        marker = text.lower()
        return "timeout" in marker or "timed out" in marker or "read timed out" in marker

    @classmethod
    def _resolve_interactive_propose_timeout(cls, agent_cfg: dict | None, *, fallback: int) -> int:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_interactive_propose_timeout
        from agent.services._task_scoped_config_policy import (
            resolve_interactive_propose_timeout,
        )
        return resolve_interactive_propose_timeout(agent_cfg, fallback=fallback)

    @classmethod
    def _resolve_interactive_retry_timeout(cls, agent_cfg: dict | None, *, fallback: int) -> int:
        # SPLIT-001c: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_interactive_retry_timeout
        from agent.services._task_scoped_config_policy import (
            resolve_interactive_retry_timeout,
        )
        return resolve_interactive_retry_timeout(agent_cfg, fallback=fallback)

    @staticmethod
    def _build_flow_metrics_payload(
        *,
        run_id: str | None,
        phase: str,
        propose_ok: bool | None,
        execute_ok: bool | None,
        artifact_created: bool | None,
        worker_profile: str | None = None,
        profile_source: str | None = None,
        policy_classification: str | None = None,
        retrieval_cache_hit: bool | None = None,
        retrieval_latency_ms: int | None = None,
        retrieval_quality_score: float | None = None,
    ) -> dict:
        # SPLIT-001j: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_citation.build_flow_metrics_payload
        from agent.services._task_scoped_citation import build_flow_metrics_payload
        return build_flow_metrics_payload(
            run_id=run_id, phase=phase, propose_ok=propose_ok, execute_ok=execute_ok,
            artifact_created=artifact_created, worker_profile=worker_profile,
            profile_source=profile_source, policy_classification=policy_classification,
            retrieval_cache_hit=retrieval_cache_hit, retrieval_latency_ms=retrieval_latency_ms,
            retrieval_quality_score=retrieval_quality_score,
        )

    @staticmethod
    def _build_planner_observability_payload(
        *,
        trigger: str | None,
        policy_decision_ref: str | None,
        plan_diff: dict | None,
    ) -> dict:
        # SPLIT-001j: delegating wrapper.
        from agent.services._task_scoped_citation import build_planner_observability_payload
        return build_planner_observability_payload(trigger=trigger, policy_decision_ref=policy_decision_ref, plan_diff=plan_diff)

    @staticmethod
    def _extract_retrieval_trace_link(context_payload: dict | None) -> dict[str, str | None]:
        # SPLIT-001j: delegating wrapper.
        from agent.services._task_scoped_citation import extract_retrieval_trace_link
        return extract_retrieval_trace_link(context_payload)

    # --- cluster: citation_grounded (source catalog, citation contract, flow metrics) ---
    def _build_source_catalog_from_execution_context(
        self,
        *,
        tid: str,
        task: dict,
        llm_scope: str = "local_only",
    ) -> dict | None:
        # SPLIT-001j: delegating wrapper.
        from agent.services._task_scoped_citation import build_source_catalog_from_execution_context
        return build_source_catalog_from_execution_context(tid=tid, task=task, llm_scope=llm_scope)

    @staticmethod
    def _render_citation_contract_prompt(source_catalog: dict | None) -> str:
        # SPLIT-001j: delegating wrapper.
        from agent.services._task_scoped_citation import render_citation_contract_prompt
        return render_citation_contract_prompt(source_catalog)

    @staticmethod
    def _extract_grounded_answer_payload(output: str | None) -> dict | None:
        # SPLIT-001j: delegating wrapper.
        from agent.services._task_scoped_citation import extract_grounded_answer_payload
        return extract_grounded_answer_payload(output)

    @staticmethod
    def _update_task_flow_metrics(
        *,
        tid: str,
        task: dict,
        flow_metrics: dict,
    ) -> None:
        # SPLIT-001j: delegating wrapper.
        from agent.services._task_scoped_citation import update_task_flow_metrics
        return update_task_flow_metrics(tid=tid, task=task, flow_metrics=flow_metrics)

    @staticmethod
    def _invoke_cli_runner(cli_runner: Callable, **cli_kwargs):
        signature_target = cli_runner
        side_effect = getattr(cli_runner, "side_effect", None)
        if callable(side_effect):
            signature_target = side_effect
        try:
            signature = inspect.signature(signature_target)
        except (TypeError, ValueError):
            return cli_runner(**cli_kwargs)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return cli_runner(**cli_kwargs)
        filtered_kwargs = {key: value for key, value in cli_kwargs.items() if key in signature.parameters}
        return cli_runner(**filtered_kwargs)

    # --- cluster: propose + execute (public API) + task_aux ---
    def propose_task_step(
        self,
        tid: str,
        request_data,
        *,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse:
        task = self._require_task(tid)
        terminal_guard = self._terminal_parent_goal_guard(tid=tid, task=task, phase="propose")
        if terminal_guard is not None:
            return terminal_guard
        forwarded = self._forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=f"/tasks/{tid}/step/propose",
            payload=request_data.model_dump(),
            forwarder=forwarder,
            on_success=lambda response, loaded_task: self._persist_forwarded_proposal(
                response,
                loaded_task,
                request_payload=request_data.model_dump(),
            ),
        )
        if forwarded is not None:
            return forwarded

        scoped_resolution = get_goal_config_runtime_service().get_effective_config(
            goal_id=str(task.get("goal_id") or "").strip() or None,
            task_id=tid,
        )
        base_cfg = {}
        if has_app_context():
            base_cfg = dict((current_app.config.get("AGENT_CONFIG") or {}))
        scoped_cfg = dict(scoped_resolution.config or {})
        # Keep explicit scoped overrides, but do not erase valid app-level defaults with None values.
        cfg = {**base_cfg, **{k: v for k, v in scoped_cfg.items() if v is not None}}
        base_prompt = request_data.prompt or task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"
        source_catalog = self._build_source_catalog_from_execution_context(
            tid=tid,
            task=task,
            llm_scope="local_only",
        )
        if not isinstance(source_catalog, dict):
            existing_source_catalog = dict((task.get("verification_status") or {}).get("source_catalog") or {})
            if existing_source_catalog:
                source_catalog = {
                    "catalog_id": existing_source_catalog.get("source_catalog_id"),
                    "catalog_hash": existing_source_catalog.get("source_catalog_hash"),
                    "sources": list(existing_source_catalog.get("sources") or []),
                }
        citation_contract = self._render_citation_contract_prompt(source_catalog)
        explicit_task_kind = str(task.get("task_kind") or "").strip().lower()
        task_kind = explicit_task_kind or normalize_task_kind(None, base_prompt)
        rc_input = getattr(request_data, "research_context", None)
        if rc_input is None:
            stored = dict((task or {}).get("worker_execution_context") or {}).get("research_context_input")
            if stored:
                rc_input = stored
        research_context_summary = get_research_context_bridge_service().build_context(
            task=task,
            research_context=rc_input,
            query=base_prompt,
        )
        # Research proposals already have a dedicated CLI-backed research result
        # path. Route them there directly so we do not run generic LLM proposal
        # strategies first and risk a slow or unreachable model HTTP call.
        if task_kind == "research" and not str(getattr(request_data, "strategy_mode", "") or "").strip():
            return self._propose_single_task_step(
                tid=tid,
                task=task,
                request_data=request_data,
                base_prompt=base_prompt,
                research_context=research_context_summary,
                cli_runner=cli_runner,
                cfg=cfg,
                tool_definitions_resolver=tool_definitions_resolver,
                allow_legacy_path=True,
            )
        explicit_task_kind = str(
            task.get("task_kind")
            or getattr(request_data, "task_kind", "")
            or ""
        ).strip().lower()
        legacy_cli_task_kinds = {
            "generic",
            "analysis",
            "coding",
            "implementation",
            "ops",
            "testing",
            "doc",
            "review",
        }
        if explicit_task_kind and task_kind in legacy_cli_task_kinds and not str(getattr(request_data, "strategy_mode", "") or "").strip():
            routed_backend, _routing_reason = self._resolve_cli_backend(
                task_kind,
                requested_backend="auto",
                agent_cfg=cfg,
                required_capabilities=derive_required_capabilities(task, task_kind),
            )
            if routed_backend in SUPPORTED_CLI_BACKENDS:
                return self._propose_single_task_step(
                    tid=tid,
                    task=task,
                    request_data=request_data,
                    base_prompt=base_prompt,
                    research_context=research_context_summary,
                    cli_runner=cli_runner,
                    cfg=cfg,
                    tool_definitions_resolver=tool_definitions_resolver,
                    allow_legacy_path=True,
                )
        strategy_mode = str(getattr(request_data, "strategy_mode", "") or "").strip().lower()
        if not strategy_mode:
            if list(getattr(request_data, "providers", None) or []):
                worker_profile, profile_source = resolve_worker_execution_profile(
                    worker_execution_context=(task.get("worker_execution_context") or {}),
                    agent_cfg=cfg,
                )
                return self._propose_task_with_comparisons(
                    tid=tid,
                    task=task,
                    request_data=request_data,
                    prompt=base_prompt,
                    base_prompt=base_prompt,
                    worker_context_meta={
                        "worker_profile": worker_profile,
                        "profile_source": profile_source,
                    },
                    research_context=research_context_summary,
                    cli_runner=cli_runner,
                    cfg=cfg,
                )
            if explicit_task_kind and not get_task_kind_preset(explicit_task_kind):
                handler_response = self._try_handler_propose(
                    tid=tid,
                    task=task,
                    task_kind=explicit_task_kind,
                    request_data=request_data,
                    base_prompt=base_prompt,
                    cli_runner=cli_runner,
                    forwarder=forwarder,
                    tool_definitions_resolver=tool_definitions_resolver,
                )
                if handler_response is not None:
                    return handler_response
            legacy_enabled = bool(((cfg.get("task_scoped_execution") or {}).get("allow_legacy_single_step_path", False)))
            if legacy_enabled and task_kind in legacy_cli_task_kinds and has_app_context():
                return self._propose_single_task_step(
                    tid=tid,
                    task=task,
                    request_data=request_data,
                    base_prompt=base_prompt,
                    research_context=research_context_summary,
                    cli_runner=cli_runner,
                    cfg=cfg,
                    tool_definitions_resolver=tool_definitions_resolver,
                    allow_legacy_path=True,
                )
        from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext
        from worker.core.propose import ExecutableProposal, validate_executable_proposal
        from agent.services.propose_strategy_registry import build_strategy_registry

        propose_policy_override = task.get("propose_policy_override", {})
        task_override = {}
        if getattr(request_data, "strategy_mode", None):
            task_override["strategy_mode"] = str(getattr(request_data, "strategy_mode")).strip().lower()
        policy = get_propose_policy_service().get_effective_policy(
            task_kind=task_kind,
            task_override=task_override or None,
            project_config=cfg,
            admin_overrides=propose_policy_override
        )
        compaction_payload = None
        compaction_meta = None
        if bool(getattr(policy, "context_compaction_enabled", True)):
            mode_data = {}
            if isinstance(task.get("mode_data"), dict):
                mode_data = {**mode_data, **dict(task.get("mode_data") or {})}
            if isinstance((task.get("worker_execution_context") or {}).get("mode_data"), dict):
                mode_data = {**mode_data, **dict((task.get("worker_execution_context") or {}).get("mode_data") or {})}
            llm_cfg = dict(cfg.get("llm_config") or {})
            planning_policy = dict(cfg.get("planning_policy") or {})
            compacted = get_planning_context_compactor_service().compact(
                goal_text=str(base_prompt or ""),
                context_text=str((research_context_summary or {}).get("prompt_section") or ""),
                mode=str(task_kind or "generic"),
                mode_data=mode_data,
                planning_policy=planning_policy,
                llm_config=llm_cfg,
                policy=policy,
            )
            compaction_payload = dict(compacted.payload or {})
            compaction_meta = dict(compacted.meta or {})
            _c_status = str(compaction_meta.get("status") or "").strip().lower()
            if _c_status == "success":
                record_product_event(
                    "planning_context_compaction_succeeded",
                    actor="task_scoped_execution_service",
                    details={
                        "task_id": tid,
                        "status": _c_status,
                        "input_chars": compaction_meta.get("input_chars"),
                        "output_chars": compaction_meta.get("output_chars"),
                        "reduction_ratio": compaction_meta.get("reduction_ratio"),
                    },
                    goal_id=str(task.get("goal_id") or "") or None,
                )
            elif _c_status in {"fallback", "bypassed"}:
                record_product_event(
                    "planning_context_compaction_fallback_used",
                    actor="task_scoped_execution_service",
                    details={
                        "task_id": tid,
                        "status": _c_status,
                        "error_classification": compaction_meta.get("error_classification"),
                    },
                    goal_id=str(task.get("goal_id") or "") or None,
                )
            elif _c_status == "failed":
                record_product_event(
                    "planning_context_compaction_failed",
                    actor="task_scoped_execution_service",
                    details={
                        "task_id": tid,
                        "status": _c_status,
                        "error_classification": compaction_meta.get("error_classification"),
                    },
                    goal_id=str(task.get("goal_id") or "") or None,
                )
            if (
                str(compaction_meta.get("status") or "").strip().lower() == "failed"
                and bool(getattr(policy, "context_compaction_required", False))
                and not bool(getattr(policy, "context_compactor_fail_open", False))
            ):
                return TaskScopedRouteResponse(
                    status="error",
                    message="planning_context_compaction_failed",
                    data={
                        "task_id": tid,
                        "status": "failed",
                        "context_compaction": compaction_meta,
                    },
                    code=422,
                )
        system_prompt = self._get_system_prompt_for_task(tid)
        assembled_instruction = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            emit_audit=False,
        )
        instruction_stack_payload = dict(assembled_instruction.get("instruction_stack") or {})
        instruction_diagnostics = dict(assembled_instruction.get("diagnostics") or {})
        rendered_system_prompt = str(assembled_instruction.get("rendered_system_prompt") or "").strip() or None

        strategies = build_strategy_registry()

        orch = ProposeStrategyOrchestrator(policy, strategies)
        # APRL-011/013: resolve active profile; reuse from previous proposal if stable
        _active_profile_meta: dict | None = dict(instruction_diagnostics.get("active_agent_profile") or {}) or None

        context = ProposeContext(
            goal_id=task.get("goal_id", "unknown"),
            task_id=tid,
            task=task,
            base_prompt=base_prompt,
            research_context=research_context_summary,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            policy=policy,
            effective_config=cfg or None,
            instruction_stack=instruction_stack_payload or None,
            rendered_system_prompt=rendered_system_prompt,
            instruction_diagnostics=instruction_diagnostics or None,
            planning_context_compaction=compaction_payload,
            planning_context_compaction_meta=compaction_meta,
            active_agent_profile=_active_profile_meta,
        )
        if citation_contract:
            context.base_prompt = f"{context.base_prompt}\n\n{citation_contract}"
        had_llm_goal_id = False
        had_llm_task_id = False
        previous_llm_goal_id = None
        previous_llm_task_id = None
        if has_request_context():
            had_llm_goal_id = hasattr(g, "llm_goal_id")
            had_llm_task_id = hasattr(g, "llm_task_id")
            previous_llm_goal_id = getattr(g, "llm_goal_id", None)
            previous_llm_task_id = getattr(g, "llm_task_id", None)
            g.llm_goal_id = str(task.get("goal_id") or "").strip() or None
            g.llm_task_id = str(tid or "").strip() or None
        try:
            result = orch.run(context)
        finally:
            if has_request_context():
                if had_llm_goal_id:
                    g.llm_goal_id = previous_llm_goal_id
                else:
                    try:
                        delattr(g, "llm_goal_id")
                    except Exception:
                        pass
                if had_llm_task_id:
                    g.llm_task_id = previous_llm_task_id
                else:
                    try:
                        delattr(g, "llm_task_id")
                    except Exception:
                        pass
        result_dict = result.to_dict()
        if not result.is_executable:
            fallback_backend, fallback_reason = self._resolve_cli_backend(
                task_kind,
                requested_backend="auto",
                agent_cfg=cfg,
                required_capabilities=derive_required_capabilities(task, task_kind),
            )
            cli_backend = fallback_backend if fallback_backend != "ananta-worker" else "aider"
            try:
                rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(
                    cli_runner,
                    prompt=str(base_prompt or ""),
                    options=["--no-interaction"],
                    timeout=self._resolve_task_propose_timeout(cfg, task_kind),
                    backend=cli_backend,
                    model=getattr(request_data, "model", None),
                    routing_policy={
                        "mode": "adaptive",
                        "task_kind": task_kind,
                        "policy_version": "v1",
                        "routing_reason": fallback_reason,
                        "fallback_backend": cli_backend,
                    },
                    session=None,
                    workdir=None,
                )
                raw_res, _output_source = self._coalesce_cli_output(cli_out, cli_err)
                parsed = json.loads(str(raw_res or "{}"))
                fallback_command = str(parsed.get("command") or "").strip() or None
                fallback_tool_calls = parsed.get("tool_calls") if isinstance(parsed.get("tool_calls"), list) else []
                fallback_reason = str(parsed.get("reason") or result.reason or "fallback_cli_proposal").strip()
                # Accept stderr JSON even on non-zero exit if stdout was empty and
                # stderr contained parseable JSON with a command (stderr-fallback path).
                _usable = rc == 0 or (rc != 0 and _output_source == "stderr" and bool(fallback_command or fallback_tool_calls))
                if _usable and (fallback_command or fallback_tool_calls):
                    result_dict["command"] = fallback_command
                    result_dict["tool_calls"] = fallback_tool_calls
                    result_dict["reason"] = fallback_reason
                    result_dict["backend"] = backend_used
                    result_dict["status"] = "executable"
            except Exception:
                pass

        # Persist to last_proposal so execute step and API can read it.
        _sgpt_routing = cfg.get("sgpt_routing") if isinstance(cfg.get("sgpt_routing"), dict) else {}
        _backend_map = _sgpt_routing.get("task_kind_backend") if isinstance(_sgpt_routing.get("task_kind_backend"), dict) else {}
        _runtime_backend = str(_backend_map.get(task_kind) or _backend_map.get("*") or "").strip() or None
        propose_strategy_meta = {
            "attempted_strategies": result.metadata.get("attempted_strategies", []),
            "selected_strategy": result.metadata.get("selected_strategy"),
            "proposal_status": result.status,
            "proposal_reason": result.reason,
            "normalization_format": result.metadata.get("source_format"),
            "effective_strategy_mode": getattr(policy, "effective_strategy_mode", None) or task_override.get("strategy_mode"),
            "goal_config_source": scoped_resolution.source,
            "runtime_selection": {
                "provider": cfg.get("default_provider"),
                "model": cfg.get("default_model"),
                "backend": _runtime_backend,
                "source": scoped_resolution.source,
            },
            "instruction_stack": {
                "present": bool(instruction_stack_payload),
                "checksum": str(instruction_stack_payload.get("checksum") or "").strip() or None,
                "applied_layers_count": len(list(instruction_diagnostics.get("applied_layers") or [])),
                "suppressed_layers_count": len(list(instruction_diagnostics.get("suppressed_layers") or [])),
            },
            "active_agent_profile": _active_profile_meta,
            "planning_context_compaction": {
                "used": bool(compaction_meta is not None),
                "status": (compaction_meta or {}).get("status"),
                "reduction_ratio": (compaction_meta or {}).get("reduction_ratio"),
                "error_classification": (compaction_meta or {}).get("error_classification"),
                "input_chars": (compaction_meta or {}).get("input_chars"),
                "output_chars": (compaction_meta or {}).get("output_chars"),
            },
            "source_catalog_id": (source_catalog or {}).get("catalog_id") if isinstance(source_catalog, dict) else None,
            "source_catalog_hash": (source_catalog or {}).get("catalog_hash") if isinstance(source_catalog, dict) else None,
            "answer_schema": "grounded_answer.v1",
        }
        proposal_meta = dict(getattr(result.proposal, "metadata", None) or {}) if result.proposal is not None else {}
        proposal_provider = str(proposal_meta.get("provider") or "").strip() or None
        proposal_model = str(proposal_meta.get("model") or "").strip() or None
        strategy_id = str(getattr(result, "strategy_id", "") or "").strip() or None
        real_llm_call_profile = list((result.metadata or {}).get("llm_call_profile") or [])
        if not real_llm_call_profile:
            real_llm_call_profile = list(proposal_meta.get("llm_call_profile") or [])
        llm_call_profile = [normalize_llm_call_profile_entry(entry) for entry in real_llm_call_profile if isinstance(entry, dict)]
        if not llm_call_profile and self._allow_synthetic_llm_profile_fallback():
            # Bridge fallback: preserves correlation for diagnostics when strategy does not expose real call metrics yet.
            llm_call_profile = [
                {
                    "name": f"propose_{strategy_id or 'orchestrator'}",
                    "backend": "orchestrator",
                    "provider": proposal_provider,
                    "model": proposal_model,
                    "success": True,
                    "latency_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "source": "orchestrator_synthetic",
                    "estimated": True,
                    "error_type": None,
                    "error_message": None,
                    "started_at": None,
                    "ended_at": None,
                }
            ]
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "stderr_preview": None,
            "output_source": "orchestrator",
            **({"llm_call_profile": llm_call_profile} if llm_call_profile else {}),
        }
        resolved_reason = str(result_dict.get("reason") or result.reason or "").strip() or result.reason
        resolved_command = result_dict.get("command") if isinstance(result_dict.get("command"), str) else (
            result.proposal.command if result.is_executable and result.proposal is not None else None
        )
        resolved_tool_calls = (
            list(result_dict.get("tool_calls") or [])
            if isinstance(result_dict.get("tool_calls"), list)
            else ((result.proposal.tool_calls or []) if result.is_executable and result.proposal is not None else [])
        )
        resolved_backend = str(result_dict.get("backend") or "orchestrator").strip() or "orchestrator"
        get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=resolved_reason,
            raw=None,
            backend=resolved_backend,
            model=None,
            routing={
                "task_kind": task_kind,
                "propose_strategy_meta": propose_strategy_meta,
                "goal_config_source": scoped_resolution.source,
            },
            cli_result=cli_result,
            worker_context={"strategy": result.metadata.get("selected_strategy")},
            trace={"policy_version": "v1"},
            review=None,
            command=resolved_command,
            tool_calls=resolved_tool_calls,
            research_context=research_context_summary,
            history_event={
                "event_type": "proposal_result",
                "reason": resolved_reason,
                "backend": resolved_backend,
                "propose_strategy_meta": propose_strategy_meta,
            },
        )
        if isinstance(source_catalog, dict):
            verification_status = dict(task.get("verification_status") or {})
            verification_status["source_catalog"] = {
                "source_catalog_id": source_catalog.get("catalog_id"),
                "source_catalog_hash": source_catalog.get("catalog_hash"),
                "source_count": len(list(source_catalog.get("sources") or [])),
                "retrieval_trace_id": source_catalog.get("retrieval_trace_id"),
                "sources": list(source_catalog.get("sources") or []),
            }
            verification_status["answer_verification"] = {
                **dict(verification_status.get("answer_verification") or {}),
                "answer_schema": "grounded_answer.v1",
                "citation_verification_status": "pending",
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "proposing"),
                verification_status=verification_status,
            )

        # Flatten resolved fields into the response so that API consumers that were
        # written against the old flat contract (command/backend/routing at top level)
        # continue to work alongside the new nested proposal structure.
        routing_dims = self._routing_dimensions(
            backend_used=resolved_backend,
            model=None,
            agent_cfg=cfg,
        )
        return TaskScopedRouteResponse(data={
            **result_dict,
            "propose_strategy_meta": propose_strategy_meta,
            "command": resolved_command,
            "backend": resolved_backend,
            "routing": {
                "effective_backend": resolved_backend,
                "task_kind": task_kind,
                "goal_config_source": scoped_resolution.source,
                **routing_dims,
            },
        })

    def execute_task_step(
        self,
        tid: str,
        request_data,
        *,
        forwarder: Callable,
        cli_runner: Callable | None = None,
        tool_definitions_resolver: Callable | None = None,
    ) -> TaskScopedRouteResponse:
        task = self._require_task(tid)
        terminal_guard = self._terminal_parent_goal_guard(tid=tid, task=task, phase="execute")
        if terminal_guard is not None:
            return terminal_guard
        forwarded = self._forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=f"/tasks/{tid}/step/execute",
            payload=request_data.model_dump(),
            forwarder=forwarder,
            on_success=lambda response, loaded_task: self._persist_forwarded_execution(
                tid=tid,
                response=response,
                task=loaded_task,
                request_data=request_data,
            ),
        )
        if forwarded is not None:
            return forwarded

        explicit_task_kind = str(
            getattr(request_data, "task_kind", None)
            or ((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind")
            or task.get("task_kind")
            or ""
        ).strip().lower()
        task_kind = explicit_task_kind or normalize_task_kind(
            None,
            request_data.command or task.get("description") or task.get("prompt") or "",
        )
        handler_response = self._try_handler_execute(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            forwarder=forwarder,
        )
        if handler_response is not None:
            return handler_response

        # HF-T023: Hermes is proposal/review-only in phase 1 — block mutation paths
        requested_backend = str(getattr(request_data, "requested_backend", None) or "").strip().lower()
        if requested_backend == "hermes":
            return TaskScopedRouteResponse(
                data={
                    "status": "denied",
                    "reason": "hermes_phase1_no_execute_mutation",
                    "task_id": tid,
                    "task_kind": task_kind,
                    "backend": "hermes",
                },
                status="denied",
                message="Hermes cannot execute mutation tasks in phase 1",
                code=403,
            )

        scoped_resolution = get_goal_config_runtime_service().get_effective_config(
            goal_id=str(task.get("goal_id") or "").strip() or None,
            task_id=tid,
        )
        agent_cfg = dict(scoped_resolution.config or {})
        execution_policy = get_core_services().task_execution_service.resolve_policy(
            request_data,
            agent_cfg=agent_cfg,
            source="task_execute",
        )

        command = request_data.command
        tool_calls = request_data.tool_calls
        reason = "Direkte Ausführung"
        used_last_proposal = False
        proposal_meta = dict(task.get("last_proposal") or {})
        proposal_routing = dict(proposal_meta.get("routing") or {})
        proposal_worker_context = dict(proposal_meta.get("worker_context") or {})
        worker_profile = normalize_worker_execution_profile(
            proposal_worker_context.get("worker_profile") or proposal_routing.get("worker_profile")
        )
        profile_source = str(
            proposal_worker_context.get("profile_source") or proposal_routing.get("profile_source") or "agent_default"
        ).strip().lower() or "agent_default"
        policy_classification_summary = str(
            proposal_routing.get("policy_classification_summary") or proposal_routing.get("reason") or ""
        ).strip().lower() or None

        if not command and not tool_calls:
            proposal = task.get("last_proposal")
            if not proposal:
                raise TaskConflictError("no_proposal")
            research_artifact = proposal.get("research_artifact") if isinstance(proposal, dict) else None
            if isinstance(research_artifact, dict):
                return self._execute_research_artifact(
                    tid=tid,
                    task=task,
                    proposal=proposal,
                    research_artifact=research_artifact,
                    execution_policy=execution_policy,
                )
            try:
                from worker.core.propose import validate_executable_proposal
                command, tool_calls, _reason = validate_executable_proposal(proposal)
                reason = _reason or proposal.get("reason", "ExecutableProposal executed")
            except (ValueError, TypeError) as ve:
                return TaskScopedRouteResponse(
                    data={
                        "status": "denied",
                        "reason": "invalid_executable_proposal_format",
                        "task_id": tid,
                        "proposal_preview": str(proposal)[:200],
                        "validation_errors": [str(ve)],
                    },
                    status="denied",
                    message="ExecutableProposal validation failed",
                    code=400,
                )
            used_last_proposal = True

        if task_kind == "domain_action":
            return self._execute_domain_action(
                tid=tid,
                task=task,
                task_kind=task_kind,
                request_data=request_data,
                command=command,
                reason=reason,
                execution_policy=execution_policy,
            )

        if command == _INTERACTIVE_TERMINAL_FINALIZE_COMMAND:
            return self._finalize_interactive_terminal_execution(
                tid=tid,
                task=task,
                reason=reason,
                execution_policy=execution_policy,
            )

        exec_started_at = time.time()
        workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
        lock_ok, lock_reason = get_worker_workspace_service().acquire_output_dir_lock(task=task, workspace_dir=workspace_ctx.workspace_dir)
        if not lock_ok:
            return TaskScopedRouteResponse(
                data={"status": "blocked", "reason_code": lock_reason or "workspace_write_conflict", "task_id": tid},
                status="blocked",
                message="Shared output directory is currently locked",
                code=409,
            )
        context_delivery_result = None
        if workspace_ctx.context_policy is not None and getattr(workspace_ctx.context_policy, "scope_mode", "full") != "full":
            try:
                from agent.services.context_delivery_service import get_context_delivery_service
                context_delivery_result = get_context_delivery_service().deliver(task=task, workspace_ctx=workspace_ctx)
            except Exception as _csd_err:
                return TaskScopedRouteResponse(
                    data={"status": "failed", "error": "context_delivery_failed", "detail": str(_csd_err), "task_id": tid},
                    status="failed",
                    message="Context delivery failed",
                    code=500,
                )
        try:
            before_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
            command, runtime_command_rewrite = self._rewrite_runtime_command_for_workspace_tools(
                command=command,
                workspace_dir=str(workspace_ctx.workspace_dir),
            )
            pipeline = new_pipeline_trace(
                pipeline="task_execute",
                task_kind=((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind"),
                policy_version=((task.get("last_proposal", {}) or {}).get("trace") or {}).get("policy_version"),
                metadata={"task_id": tid},
            )
            native_artifact_refs: list[dict] = []
            execution_repair_meta: dict | None = None
            if self._should_use_native_worker_runtime(proposal_meta=proposal_meta, agent_cfg=agent_cfg, command=command):
                append_stage(
                    pipeline,
                    name="native_worker_execute",
                    status="ok",
                    metadata={"runtime_path": "native_worker_pipeline"},
                )
                native_execution = get_native_worker_runtime_service().execute_and_verify_command(
                    tid=tid,
                    task=task,
                    command=str(command or ""),
                    trace_id=str(((proposal_meta.get("trace") or {}).get("trace_id") or f"native-exec-{tid}")),
                    worker_profile=worker_profile,
                    profile_source=profile_source,
                    timeout_seconds=int(execution_policy.timeout_seconds),
                    workspace_dir=workspace_ctx.workspace_dir,
                    native_runtime_payload=(proposal_worker_context.get("native_runtime") if isinstance(proposal_worker_context.get("native_runtime"), dict) else {}),
                    agent_cfg=agent_cfg,
                )
                execution_run = LocalExecutionResult(
                    output=str(native_execution.get("output") or ""),
                    exit_code=int(native_execution.get("exit_code") or 1),
                    retries_used=0,
                    failure_type=str(native_execution.get("failure_type") or "native_worker_runtime"),
                    retry_history=[],
                    status=str(native_execution.get("status") or "failed"),
                    loop_signals=[],
                    loop_detection=None,
                    approval_decision=dict(native_execution.get("approval_decision") or {}),
                )
                native_artifact_refs = [ref for ref in list(native_execution.get("artifact_refs") or []) if isinstance(ref, dict)]
                execution_repair_meta = {
                    "native_worker_runtime": dict(native_execution.get("native_runtime") or {}),
                    "runtime_path": "native_worker_pipeline",
                }
                native_policy_summary = str(native_execution.get("policy_classification_summary") or "").strip().lower() or None
                if native_policy_summary:
                    policy_classification_summary = native_policy_summary
            else:
                execution_run = get_core_services().task_execution_service.execute_local_step(
                    tid=tid,
                    task=task,
                    command=command,
                    tool_calls=tool_calls,
                    execution_policy=execution_policy,
                    guard_cfg=agent_cfg,
                    pipeline=pipeline,
                    exec_started_at=exec_started_at,
                    working_directory=str(workspace_ctx.workspace_dir),
                )
                if used_last_proposal and cli_runner and (
                    self._is_shell_meta_blocked_failure(execution_run.output, execution_run.failure_type)
                    or self._is_command_not_found_failure(execution_run.output, execution_run.failure_type)
                ):
                    repaired_execution = self._attempt_repaired_execute_after_meta_block(
                        tid=tid,
                        task=task,
                        task_kind=task_kind,
                        command=command,
                        execution_output=execution_run.output,
                        execution_policy=execution_policy,
                        agent_cfg=agent_cfg,
                        cli_runner=cli_runner,
                        tool_definitions_resolver=tool_definitions_resolver,
                        pipeline=pipeline,
                        workspace_dir=str(workspace_ctx.workspace_dir),
                        exec_started_at=exec_started_at,
                    )
                    if repaired_execution:
                        command = repaired_execution["command"]
                        tool_calls = repaired_execution["tool_calls"]
                        reason = repaired_execution["reason"]
                        execution_run = repaired_execution["execution_run"]
                        execution_repair_meta = repaired_execution["repair_meta"]
            after_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
            changed_files = get_worker_workspace_service().detect_changed_files(before_workspace_snapshot, after_workspace_snapshot)
            meaningful_changed_files = get_worker_workspace_service().filter_meaningful_changed_files(changed_files)
            # FA-T014: Explicit FileChangeSet collection
            from worker.core.file_change_set import diff_snapshots
            from pathlib import Path
            before_id = hashlib.sha256(str(sorted(before_workspace_snapshot.items())).encode()).hexdigest()[:16]
            after_id = hashlib.sha256(str(sorted(after_workspace_snapshot.items())).encode()).hexdigest()[:16]
            exec_id = f"exec-{tid}-{int(time.time()*1000)}"
            fcs = diff_snapshots(
                task_id=tid,
                execution_id=exec_id,
                workspace_root=Path(workspace_ctx.workspace_dir),
                before_snapshot_id=before_id,
                before_snapshot=before_workspace_snapshot,
                after_snapshot_id=after_id,
                after_snapshot=after_workspace_snapshot,
            )
            git_pushed: bool = False
            git_ctx = getattr(workspace_ctx, "git_context", None)
            if git_ctx is not None and getattr(git_ctx, "is_clone", False) and meaningful_changed_files:
                try:
                    from agent.services.workspace_git_service import get_workspace_git_service
                    git_pushed = bool(get_workspace_git_service().commit_and_push(
                        git_ctx.workspace_dir,
                        branch=git_ctx.branch,
                        message=f"task {str(tid)[:12]}: {str(task.get('title') or tid)[:60]}",
                    ))
                except Exception as _git_push_err:
                    logging.warning("git commit+push failed for task %s: %s", tid, _git_push_err)
            workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
                task_id=tid,
                task=task,
                workspace_dir=workspace_ctx.workspace_dir,
                changed_rel_paths=changed_files,
                sync_cfg=workspace_ctx.artifact_sync,
            )
            combined_artifact_refs = [*list(workspace_artifact_refs or []), *list(native_artifact_refs or [])]
            execution_duration_ms = int((time.time() - exec_started_at) * 1000)
            tool_run_refs: list[dict] = []
            try:
                from agent.services.tool_run_catalog_service import get_tool_run_catalog_service

                run_entry = get_tool_run_catalog_service().build_run_entry(
                    task_id=str(tid),
                    index=1,
                    tool_name="shell",
                    command=str(command or ""),
                    exit_code=int(execution_run.exit_code),
                    stdout=str(execution_run.output or ""),
                    stderr="",
                    artifact_paths=[
                        str(item.get("path") or item.get("artifact_path") or "")
                        for item in list(combined_artifact_refs or [])
                        if isinstance(item, dict)
                    ],
                    started_at=exec_started_at,
                    ended_at=time.time(),
                )
                tool_run_refs = [run_entry]
            except Exception:
                tool_run_refs = []
            proposal_meta = task.get("last_proposal", {}) or {}
            trace = build_trace_record(
                task_id=tid,
                event_type="execution_result",
                task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
                backend=proposal_meta.get("backend"),
                requested_backend=proposal_meta.get("backend"),
                routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
                policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
                metadata={
                    "retries_used": execution_run.retries_used,
                    "duration_ms": execution_duration_ms,
                    "failure_type": execution_run.failure_type,
                },
            )
            if execution_run.status == "completed":
                from agent.metrics import TASK_COMPLETED

                TASK_COMPLETED.inc()
            else:
                from agent.metrics import TASK_FAILED

                TASK_FAILED.inc()

            response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
                tid=tid,
                task=task,
                status=execution_run.status,
                reason=reason,
                command=command,
                tool_calls=tool_calls,
                output=execution_run.output,
                exit_code=execution_run.exit_code,
                retries_used=execution_run.retries_used,
                retry_history=execution_run.retry_history,
                failure_type=execution_run.failure_type,
                execution_duration_ms=execution_duration_ms,
                trace=trace,
                pipeline={**pipeline, "trace_id": trace["trace_id"]},
                execution_policy=execution_policy,
                artifact_refs=combined_artifact_refs or None,
                extra_history={
                    "workspace_changed_files": changed_files,
                    "workspace_meaningful_changed_files": meaningful_changed_files,
                    "file_change_set": fcs.to_dict(),
                    "workspace_dir": str(workspace_ctx.workspace_dir),
                    "workspace_artifact_count": len(workspace_artifact_refs),
                    "native_artifact_count": len(native_artifact_refs),
                    "workspace_state_sync": _build_workspace_state_sync_record(
                        task=task,
                        materialization_manifest=workspace_ctx.materialization_manifest,
                        workspace_artifact_refs=workspace_artifact_refs,
                        git_pushed=git_pushed,
                    ),
                    "loop_signals": execution_run.loop_signals,
                    "loop_detection": execution_run.loop_detection,
                    "approval_decision": execution_run.approval_decision,
                    "execution_repair": execution_repair_meta,
                    "tool_run_refs": tool_run_refs,
                    "runtime_command_rewrite": runtime_command_rewrite,
                    "flow_metrics": self._build_flow_metrics_payload(
                        run_id=str((((task.get("last_proposal") or {}).get("trace") or {}).get("trace_id") or "")),
                        phase="execute",
                        propose_ok=True,
                        execute_ok=execution_run.status == "completed",
                        artifact_created=bool(meaningful_changed_files),
                        worker_profile=worker_profile,
                        profile_source=profile_source,
                        policy_classification=policy_classification_summary,
                    ),
                },
            )
            if execution_run.status == "completed":
                worker_execution_contract = dict(task.get("worker_execution_contract") or {})
                expected_paths = [
                    str(item.get("relative_path") or "").strip()
                    for item in list(worker_execution_contract.get("expected_artifacts") or [])
                    if isinstance(item, dict) and bool(item.get("required", True)) and str(item.get("relative_path") or "").strip()
                ]
                artifact_ids = [str(ref.get("artifact_id") or "").strip() for ref in list(combined_artifact_refs or []) if str(ref.get("artifact_id") or "").strip()]
                produced_paths = {
                    str(ref.get("workspace_relative_path") or "").strip()
                    for ref in list(combined_artifact_refs or [])
                    if isinstance(ref, dict) and str(ref.get("workspace_relative_path") or "").strip()
                }
                missing = [path for path in expected_paths if path not in produced_paths]
                collection_result = {
                    "manifest_valid": not missing,
                    "artifact_ids": artifact_ids,
                    "manifest_id": f"manifest-{tid}",
                    "missing_expected_paths": missing,
                }
                final_status = apply_artifact_first_completion(
                    tid,
                    collection_result=collection_result,
                    advisory_parse_result=None,
                    exit_code=execution_run.exit_code,
                    retry_count=int(execution_run.retries_used or 0),
                    expected_paths=expected_paths,
                    verification_required=bool(expected_paths),
                    allow_synthesized_manifest=False,
                )
                response_payload["status"] = final_status
                response_payload["artifact_completion"] = {
                    "expected_paths": expected_paths,
                    "produced_paths": sorted(produced_paths),
                    "missing_expected_paths": missing,
                    "final_status": final_status,
                }
                goal_output_artifacts = self._register_goal_artifact_outputs(
                    task=task,
                    tid=tid,
                    artifact_refs=list(combined_artifact_refs or []),
                )
                if goal_output_artifacts:
                    response_payload["goal_output_artifacts"] = goal_output_artifacts

            verification_status = dict((task.get("verification_status") or {}))
            source_catalog_status = dict(verification_status.get("source_catalog") or {})
            source_catalog_sources = list(source_catalog_status.get("sources") or [])
            answer_payload = self._extract_grounded_answer_payload(response_payload.get("output"))
            answer_verification = dict(verification_status.get("answer_verification") or {})
            answer_verification.setdefault("answer_schema", "grounded_answer.v1")
            if answer_payload and source_catalog_sources:
                from agent.services.citation_verification_service import get_citation_verification_service

                verification_result = get_citation_verification_service().verify(
                    task_id=str(tid),
                    answer_payload=answer_payload,
                    source_catalog={
                        "schema": "source_catalog.v1",
                        "catalog_id": source_catalog_status.get("source_catalog_id"),
                        "task_id": str(tid),
                        "retrieval_trace_id": source_catalog_status.get("retrieval_trace_id"),
                        "retrieval_context_hash": "",
                        "retrieval_manifest_hash": "",
                        "catalog_hash": source_catalog_status.get("source_catalog_hash") or "0" * 16,
                        "sources": source_catalog_sources,
                    },
                    tool_run_catalog=tool_run_refs,
                )
                answer_verification.update(
                    {
                        "citation_verification_status": verification_result.get("status"),
                        "verified_claim_count": int(verification_result.get("verified_claim_count") or 0),
                        "unverified_claim_count": int(verification_result.get("unverified_claim_count") or 0),
                        "failed_claims": list(verification_result.get("failed_claims") or []),
                        "tool_run_refs": tool_run_refs,
                    }
                )
                if verification_result.get("status") != "verified" and str(response_payload.get("status") or "") == "completed":
                    response_payload["status"] = "failed"
            else:
                answer_verification.setdefault("citation_verification_status", "not_evaluated")
                answer_verification.setdefault("verified_claim_count", 0)
                answer_verification.setdefault("unverified_claim_count", 0)
                answer_verification.setdefault("failed_claims", [])
            verification_status["answer_verification"] = answer_verification
            update_local_task_status(
                tid,
                str(response_payload.get("status") or execution_run.status),
                verification_status=verification_status,
            )

            history_len = len(task.get("history", []) or [])
            _log_terminal_entry(current_app.config["AGENT_NAME"], history_len, "out", command=command, task_id=tid)
            _log_terminal_entry(
                current_app.config["AGENT_NAME"],
                history_len,
                "in",
                output=execution_run.output,
                exit_code=execution_run.exit_code,
                task_id=tid,
            )
            return TaskScopedRouteResponse(data=response_payload)
        finally:
            get_worker_workspace_service().release_output_dir_lock(task=task, workspace_dir=workspace_ctx.workspace_dir)

    @staticmethod
    # --- cluster: domain_action (router, comparison, single-propose, goal-artifact-output) ---
    def _build_domain_action_router() -> DomainActionRouter:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_domain_action.build_domain_action_router.
        # We import through ``self.__class__.__module__`` indirectly: tests
        # that monkeypatch ``TaskScopedExecutionService._build_domain_action_router``
        # continue to work because the patched attribute shadows this method
        # at call time, and the patched return value is returned.
        from agent.services._task_scoped_domain_action import build_domain_action_router
        return build_domain_action_router()

    def _register_goal_artifact_outputs(self, *, task: dict, tid: str, artifact_refs: list[dict]) -> list[dict]:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_domain_action.register_goal_artifact_outputs
        from agent.services._task_scoped_domain_action import register_goal_artifact_outputs
        return register_goal_artifact_outputs(
            task=task,
            tid=tid,
            artifact_refs=artifact_refs,
            get_system_prompt_for_task=self._get_system_prompt_for_task,
        )

    @staticmethod
    def _resolve_domain_action_payload(*, task: dict, command: str | None) -> dict:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_domain_action.resolve_domain_action_payload
        from agent.services._task_scoped_domain_action import resolve_domain_action_payload
        return resolve_domain_action_payload(task=task, command=command)

    def _execute_domain_action(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        command: str | None,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        # SPLIT-001e-1: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_domain_action.execute_domain_action
        from agent.services._task_scoped_domain_action import execute_domain_action
        return execute_domain_action(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            command=command,
            reason=reason,
            execution_policy=execution_policy,
        )

    def _propose_task_with_comparisons(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        prompt: str,
        base_prompt: str,
        worker_context_meta: dict,
        research_context: dict | None,
        cli_runner: Callable,
        cfg: dict,
    ) -> TaskScopedRouteResponse:
        task_kind = normalize_task_kind(None, base_prompt)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        requested_temperature = self._normalize_temperature(getattr(request_data, "temperature", None))
        timeout = self._resolve_task_propose_timeout(cfg, task_kind)
        compare_policy = resolve_execution_policy(
            TaskStepExecuteRequest(timeout=timeout),
            agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
            source="task_propose_compare",
        )
        routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]

        def _run_single_provider(provider_entry: str) -> tuple[str, dict]:
            entry = str(provider_entry or "").strip()
            if not entry:
                return provider_entry, {"error": "invalid_provider_entry"}

            parts = entry.split(":", 1)
            requested_backend = str(parts[0] or "").strip().lower()
            selected_model = self._resolve_requested_model(
                agent_cfg=cfg,
                requested_model=(parts[1].strip() if len(parts) > 1 else "") or request_data.model,
            )
            if requested_backend not in SUPPORTED_CLI_BACKENDS:
                return entry, {"error": f"unsupported_backend:{requested_backend}", "backend": requested_backend}

            effective_backend, routing_reason = self._resolve_cli_backend(
                task_kind,
                requested_backend=requested_backend,
                agent_cfg=cfg,
                required_capabilities=derive_required_capabilities(task, task_kind),
            )
            started_at = time.time()
            cli_kwargs = {
                "prompt": prompt,
                "options": ["--no-interaction"],
                "timeout": compare_policy.timeout_seconds,
                "backend": effective_backend,
                "model": selected_model,
                "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": routing_policy_version},
                "workdir": str(workspace_context.workspace_dir),
            }
            if requested_temperature is not None:
                cli_kwargs["temperature"] = requested_temperature
            if research_context:
                cli_kwargs["research_context"] = research_context
            rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
            latency_ms = int((time.time() - started_at) * 1000)
            raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
            required_capabilities = derive_required_capabilities(task, task_kind)
            routing_dimensions = self._routing_dimensions(
                backend_used=backend_used,
                model=selected_model,
                temperature=requested_temperature,
                requested_backend=requested_backend,
                agent_cfg=cfg,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
            )
            routing = {
                "task_kind": task_kind,
                "effective_backend": effective_backend,
                "reason": routing_reason,
                "policy_classification_summary": str(routing_reason or "").strip().lower() or None,
                "required_capabilities": required_capabilities,
                "research_specialization": derive_research_specialization(task, task_kind, required_capabilities),
                **routing_dimensions,
            }
            cli_result = {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "llm_call_profile": self._build_llm_call_profile_entries(
                    backend_used=backend_used,
                    model=selected_model,
                    prompt=prompt,
                    raw_output=raw_res,
                    latency_ms=latency_ms,
                    rc=rc,
                    repair_attempted=False,
                    repair_backend=None,
                    repair_model=None,
                ),
            }
            if rc != 0 and not raw_res.strip():
                return entry, {"error": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if not raw_res:
                return entry, {"error": "empty_response", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if is_research_backend(backend_used):
                research_res = self._build_research_result(
                    raw_res,
                    backend_used,
                    tid,
                    rc,
                    cli_err,
                    latency_ms,
                    output_source=output_source,
                    research_context=research_context,
                )
                research_res["model"] = selected_model
                research_res["routing"] = routing
                return entry, research_res
            command, tool_calls = self._extract_structured_action_fields(raw_res)
            return entry, {
                "reason": _extract_reason(raw_res),
                "command": command,
                "tool_calls": tool_calls,
                "raw": raw_res,
                "backend": backend_used,
                "model": selected_model,
                "routing": routing,
                "cli_result": cli_result,
            }

        results: dict[str, dict] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(request_data.providers))) as executor:
            futures = {executor.submit(_run_single_provider, provider_name): provider_name for provider_name in request_data.providers}
            for future in concurrent.futures.as_completed(futures):
                requested = futures[future]
                try:
                    provider_key, provider_result = future.result()
                    results[provider_key or requested] = provider_result
                except Exception as exc:
                    current_app.logger.error("Multi-Provider CLI Call for %s failed: %s", requested, exc)
                    results[requested] = {"error": str(exc)}

        successful_results = [
            results.get(provider_name)
            for provider_name in request_data.providers
            if isinstance(results.get(provider_name), dict) and not results.get(provider_name).get("error")
        ]
        if not successful_results:
            return TaskScopedRouteResponse(
                status="error",
                message="all_llm_failed",
                data={"comparisons": results},
                code=502,
            )

        main_res = results.get(request_data.providers[0])
        if not isinstance(main_res, dict) or main_res.get("error"):
            main_res = successful_results[0]

        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=(main_res.get("routing") or {}).get("task_kind"),
            backend=main_res.get("backend"),
            requested_backend=request_data.providers[0] if request_data.providers else "auto",
            routing_reason=((main_res.get("routing") or {}).get("reason")),
            policy_version=routing_policy_version,
            metadata={**worker_context_meta, "source": "task_propose_multi", "comparison_count": len(results)},
        )
        review = self._build_review_state(
            current_app.config.get("AGENT_CONFIG", {}) or {},
            backend=str(main_res.get("backend") or ""),
            task_kind=str(((main_res.get("routing") or {}).get("task_kind") or "")),
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
        )
        response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=main_res.get("reason"),
            raw=main_res.get("raw"),
            backend=main_res.get("backend"),
            model=main_res.get("model"),
            routing=main_res.get("routing"),
            cli_result=main_res.get("cli_result"),
            worker_context=worker_context_meta,
            trace=trace,
            review=review,
            comparisons=results,
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
            research_artifact=main_res.get("research_artifact"),
            research_context=main_res.get("research_context"),
            history_event={
                "event_type": "proposal_result",
                "reason": main_res.get("reason"),
                "backend": main_res.get("backend"),
                "routing_reason": ((main_res.get("routing") or {}).get("reason")),
                "latency_ms": int((main_res.get("cli_result") or {}).get("latency_ms") or 0),
                "returncode": int((main_res.get("cli_result") or {}).get("returncode") or 0),
                "comparison_count": len(results),
                "pipeline": None,
                "trace": trace,
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(trace.get("trace_id") or ""),
                    phase="propose",
                    propose_ok=True,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=str(((main_res.get("routing") or {}).get("reason")) or ""),
                ),
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

    def _propose_single_task_step(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        base_prompt: str,
        research_context: dict | None,
        cli_runner: Callable,
        cfg: dict,
        tool_definitions_resolver: Callable,
        allow_legacy_path: bool = False,
    ) -> TaskScopedRouteResponse:
        if not allow_legacy_path:
            raise NotImplementedError("FA-T003: legacy _propose_single_task_step is blocked for direct use.")
        task_kind = normalize_task_kind(None, base_prompt)
        required_capabilities = derive_required_capabilities(task, task_kind)
        research_specialization = derive_research_specialization(task, task_kind, required_capabilities)
        effective_backend, routing_reason = self._resolve_cli_backend(
            task_kind,
            requested_backend=None,
            agent_cfg=cfg,
            required_capabilities=required_capabilities,
        )
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        timeout = self._resolve_task_propose_timeout(cfg, task_kind)
        proposal_model = self._resolve_requested_model(
            agent_cfg=cfg,
            requested_model=getattr(request_data, "model", None),
        )
        policy_version = runtime_routing_config(cfg)["policy_version"] if isinstance(cfg, dict) else runtime_routing_config({})["policy_version"]
        session_payload = self._prepare_task_cli_session(
            tid=tid,
            task=task,
            backend=effective_backend,
            model=proposal_model,
            agent_cfg=cfg,
        )
        interactive_terminal_session = effective_backend == "opencode" and self._is_interactive_terminal_session(session_payload)
        interactive_context_profile = (
            self._resolve_interactive_context_profile(cfg, retry=False) if interactive_terminal_session else None
        )
        effective_research_context = (
            self._compact_research_context(research_context, profile=interactive_context_profile)
            if interactive_terminal_session
            else research_context
        )
        if interactive_terminal_session:
            timeout = self._resolve_interactive_propose_timeout(cfg, fallback=timeout)
        prompt_for_cli, worker_context_meta = self._build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=base_prompt,
            tool_definitions_resolver=(lambda *_args, **_kwargs: [])
            if interactive_terminal_session
            else tool_definitions_resolver,
            research_context=effective_research_context,
            interactive_terminal=interactive_terminal_session,
            context_profile=interactive_context_profile,
        )
        pipeline = new_pipeline_trace(
            pipeline="task_propose",
            task_kind=task_kind,
            policy_version=policy_version,
            metadata={"task_id": tid, "requested_backend": "auto", **worker_context_meta},
        )
        append_stage(
            pipeline,
            name="route",
            status="ok",
            metadata={"effective_backend": effective_backend, "reason": routing_reason},
        )
        requested_temperature = self._normalize_temperature(getattr(request_data, "temperature", None))
        if requested_temperature is not None:
            prompt_for_cli = (
                f"{prompt_for_cli}\n\n"
                f"[Sampling-Hinweis]\n"
                f"Ziel-Temperatur fuer diese Antwort: {requested_temperature:.2f}\n"
                + (
                    "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
                    if interactive_terminal_session
                    else "Behalte strikt das JSON-Output-Schema ein."
                )
            )
        if session_payload and not self._has_native_opencode_runtime(session_payload) and not interactive_terminal_session:
            prompt_for_cli = (
                get_cli_session_service().build_prompt_with_history(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    max_turns=int(session_payload.get("max_turns_per_session") or 40),
                )
                or prompt_for_cli
            )
        started_at = time.time()
        cli_kwargs = {
            "prompt": prompt_for_cli,
            "options": ["--no-interaction"],
            "timeout": timeout,
            "backend": effective_backend,
            "model": proposal_model,
            "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
            "session": session_payload,
            "workdir": str(workspace_context.workspace_dir),
        }
        if requested_temperature is not None:
            cli_kwargs["temperature"] = requested_temperature
        if effective_research_context:
            cli_kwargs["research_context"] = effective_research_context
        rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
        latency_ms = int((time.time() - started_at) * 1000)
        raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
        repair_meta = {"attempted": False, "backend": None, "model": None}
        interactive_retry_meta = {"attempted": False, "timeout": None, "latency_ms": None}
        append_stage(
            pipeline,
            name="execute",
            status=(
                "ok"
                if (rc == 0 if interactive_terminal_session else (rc == 0 or bool(raw_res)))
                else "error"
            ),
            metadata={
                "backend_used": backend_used,
                "returncode": rc,
                "latency_ms": latency_ms,
                "output_source": output_source,
            },
            started_at=started_at,
        )
        if interactive_terminal_session and backend_used == "opencode":
            timeout_like_failure = self._interactive_timeout_like_failure(rc=rc, output=raw_res, stderr=cli_err)
            if timeout_like_failure:
                retry_profile = self._resolve_interactive_context_profile(cfg, retry=True)
                retry_research_context = self._compact_research_context(research_context, profile=retry_profile)
                retry_prompt, retry_worker_meta = self._build_task_propose_prompt(
                    tid=tid,
                    task=task,
                    base_prompt=base_prompt,
                    tool_definitions_resolver=(lambda *_args, **_kwargs: []),
                    research_context=retry_research_context,
                    interactive_terminal=True,
                    context_profile=retry_profile,
                )
                if requested_temperature is not None:
                    retry_prompt = (
                        f"{retry_prompt}\n\n"
                        f"[Sampling-Hinweis]\n"
                        f"Ziel-Temperatur fuer diese Antwort: {requested_temperature:.2f}\n"
                        "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
                    )
                retry_timeout = self._resolve_interactive_retry_timeout(cfg, fallback=timeout)
                retry_kwargs = {
                    **cli_kwargs,
                    "prompt": retry_prompt,
                    "timeout": retry_timeout,
                }
                if retry_research_context:
                    retry_kwargs["research_context"] = retry_research_context
                started_retry = time.time()
                retry_rc, retry_out, retry_err, retry_backend = self._invoke_cli_runner(cli_runner, **retry_kwargs)
                retry_latency_ms = int((time.time() - started_retry) * 1000)
                retry_raw, retry_source = self._coalesce_cli_output(retry_out, retry_err)
                interactive_retry_meta = {
                    "attempted": True,
                    "timeout": retry_timeout,
                    "latency_ms": retry_latency_ms,
                }
                append_stage(
                    pipeline,
                    name="interactive_retry",
                    status="ok" if retry_rc == 0 else "error",
                    metadata={
                        "backend_used": retry_backend,
                        "returncode": retry_rc,
                        "latency_ms": retry_latency_ms,
                        "output_source": retry_source,
                        "timeout": retry_timeout,
                    },
                    started_at=started_retry,
                )
                rc = retry_rc
                cli_err = retry_err
                cli_out = retry_out
                raw_res = retry_raw
                output_source = retry_source
                backend_used = retry_backend
                latency_ms += retry_latency_ms
                prompt_for_cli = retry_prompt
                worker_context_meta = retry_worker_meta
                effective_research_context = retry_research_context
        if not interactive_terminal_session and rc != 0 and not raw_res.strip():
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=(cli_err or ""),
                validation_error="empty_or_failed_cli_response",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=effective_research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
            else:
                return TaskScopedRouteResponse(
                    status="error",
                    message="llm_cli_failed",
                    data={"details": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used},
                    code=502,
                )
        if not interactive_terminal_session and not raw_res:
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=(cli_err or ""),
                validation_error="empty_cli_response",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=effective_research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
            else:
                return TaskScopedRouteResponse(status="error", message="llm_failed", data={}, code=502)

        routing = {
            "task_kind": task_kind,
            "effective_backend": effective_backend,
            "reason": routing_reason,
            "policy_classification_summary": str(routing_reason or "").strip().lower() or None,
            "required_capabilities": required_capabilities,
            "research_specialization": research_specialization,
            **self._routing_dimensions(
                backend_used=backend_used,
                model=proposal_model,
                temperature=requested_temperature,
                requested_backend="auto",
                agent_cfg=cfg,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
            ),
        }
        if session_payload:
            routing["session_mode"] = "stateful"
            routing["session_id"] = session_payload["id"]
            routing["session_reused"] = bool(session_payload.get("session_reused"))
            session_metadata = session_payload.get("metadata") if isinstance(session_payload.get("metadata"), dict) else {}
            live_terminal_meta = (
                dict(session_metadata.get("opencode_live_terminal") or {})
                if isinstance(session_metadata.get("opencode_live_terminal"), dict)
                else {}
            )
            config_execution_mode = self._resolve_opencode_execution_mode(cfg)
            if (
                str(session_metadata.get("opencode_execution_mode") or "").strip().lower() in {"live_terminal", "interactive_terminal"}
                or (effective_backend == "opencode" and config_execution_mode in {"live_terminal", "interactive_terminal"})
            ):
                routing["execution_mode"] = str(session_metadata.get("opencode_execution_mode") or config_execution_mode).strip().lower()
                if not live_terminal_meta:
                    live_terminal_meta = (
                        dict((task.get("verification_status") or {}).get("opencode_live_terminal") or {})
                        if isinstance((task.get("verification_status") or {}).get("opencode_live_terminal"), dict)
                        else {}
                    )
                routing["live_terminal"] = live_terminal_meta
        if interactive_terminal_session and backend_used == "opencode":
            timeout_like_failure = self._interactive_timeout_like_failure(rc=rc, output=raw_res, stderr=cli_err)
            if rc != 0 or timeout_like_failure:
                flow_metrics = self._build_flow_metrics_payload(
                    run_id=str(session_payload.get("id") or "") if isinstance(session_payload, dict) else None,
                    phase="propose",
                    propose_ok=False,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=str(routing_reason or "").strip().lower() or None,
                )
                self._update_task_flow_metrics(tid=tid, task=task, flow_metrics=flow_metrics)
                return TaskScopedRouteResponse(
                    status="error",
                    message="llm_cli_failed",
                    data={
                        "details": cli_err or raw_res or f"backend '{backend_used}' failed with exit code {rc}",
                        "backend": backend_used,
                        "flow_metrics": flow_metrics,
                        "retry": interactive_retry_meta,
                    },
                    code=502,
                )
        if is_research_backend(backend_used):
            research_res = self._build_research_result(
                raw_res,
                backend_used,
                tid,
                rc,
                cli_err,
                latency_ms,
                output_source=output_source,
                research_context=effective_research_context,
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="proposal_result",
                task_kind=task_kind,
                backend=backend_used,
                requested_backend="auto",
                routing_reason=routing_reason,
                policy_version=policy_version,
                metadata={**worker_context_meta, "source": "task_propose", "artifact_kind": "research_report"},
            )
            pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
            response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
                tid=tid,
                task=task,
                reason=research_res.get("reason"),
                raw=raw_res,
                backend=backend_used,
                model=proposal_model,
                routing=routing,
                cli_result=research_res.get("cli_result"),
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(
                    current_app.config.get("AGENT_CONFIG", {}) or {},
                    backend_used,
                    task_kind,
                    command=None,
                    tool_calls=None,
                ),
                pipeline=pipeline_payload,
                research_artifact=research_res.get("research_artifact"),
                research_context=effective_research_context,
                history_event={
                    "event_type": "proposal_result",
                    "reason": research_res.get("reason"),
                    "backend": backend_used,
                    "routing_reason": routing_reason,
                    "latency_ms": latency_ms,
                    "returncode": rc,
                    "artifact_kind": "research_report",
                    "source_count": len((research_res.get("research_artifact") or {}).get("sources") or []),
                    "pipeline": pipeline_payload,
                    "trace": trace,
                    "flow_metrics": self._build_flow_metrics_payload(
                        run_id=str(trace.get("trace_id") or ""),
                        phase="propose",
                        propose_ok=True,
                        execute_ok=None,
                        artifact_created=None,
                        worker_profile=worker_context_meta.get("worker_profile"),
                        profile_source=worker_context_meta.get("profile_source"),
                        policy_classification=str(routing_reason or "").strip().lower() or None,
                    ),
                },
            )
            if session_payload:
                turn = get_cli_session_service().append_turn(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    output=raw_res,
                    model=proposal_model,
                    trace_id=str(trace.get("trace_id") or ""),
                    metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "research"},
                )
                if isinstance(turn, dict):
                    response_payload.setdefault("routing", {})
                    response_payload["routing"]["session_turn_id"] = turn.get("id")
            return TaskScopedRouteResponse(data=response_payload)

        if interactive_terminal_session and backend_used == "opencode":
            reason = "Interactive OpenCode session finished; finalize workspace changes"
            append_stage(
                pipeline,
                name="parse",
                status="ok",
                metadata={"interactive_terminal_finalize": True, "has_command": True, "tool_call_count": 0},
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="proposal_result",
                task_kind=task_kind,
                backend=backend_used,
                requested_backend="auto",
                routing_reason=routing_reason,
                policy_version=policy_version,
                metadata={**worker_context_meta, "source": "task_propose", "interactive_terminal": True},
            )
            pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
            cli_result = {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "repair_attempted": bool(interactive_retry_meta.get("attempted")),
                "repair_backend": backend_used if interactive_retry_meta.get("attempted") else None,
                "repair_model": proposal_model if interactive_retry_meta.get("attempted") else None,
            }
            response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
                tid=tid,
                task=task,
                reason=reason,
                raw=raw_res,
                backend=backend_used,
                model=proposal_model,
                routing=routing,
                cli_result=cli_result,
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(
                    current_app.config.get("AGENT_CONFIG", {}) or {},
                    backend_used,
                    task_kind,
                    command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                    tool_calls=None,
                ),
                pipeline=pipeline_payload,
                command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                tool_calls=None,
                history_event={
                    "event_type": "proposal_result",
                    "reason": reason,
                    "backend": backend_used,
                    "routing_reason": routing_reason,
                    "latency_ms": latency_ms,
                    "returncode": rc,
                    "interactive_terminal": True,
                    "pipeline": pipeline_payload,
                    "trace": trace,
                    "flow_metrics": self._build_flow_metrics_payload(
                        run_id=str(trace.get("trace_id") or ""),
                        phase="propose",
                        propose_ok=True,
                        execute_ok=None,
                        artifact_created=None,
                        worker_profile=worker_context_meta.get("worker_profile"),
                        profile_source=worker_context_meta.get("profile_source"),
                        policy_classification=str(routing_reason or "").strip().lower() or None,
                    ),
                },
            )
            if session_payload:
                turn = get_cli_session_service().append_turn(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    output=raw_res,
                    model=proposal_model,
                    trace_id=str(trace.get("trace_id") or ""),
                    metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "interactive_terminal"},
                )
                if isinstance(turn, dict):
                    response_payload.setdefault("routing", {})
                    response_payload["routing"]["session_turn_id"] = turn.get("id")
            _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt_for_cli, task_id=tid)
            _log_terminal_entry(
                current_app.config["AGENT_NAME"],
                0,
                "out",
                reason=reason,
                command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                tool_calls=None,
                task_id=tid,
            )
            return TaskScopedRouteResponse(data=response_payload)

        reason = _extract_reason(raw_res)
        command, tool_calls = self._extract_structured_action_fields(raw_res)
        if not command and not tool_calls:
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=raw_res,
                validation_error="missing_required_fields: command_or_tool_calls",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=effective_research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                reason = _extract_reason(raw_res)
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
                command, tool_calls = self._extract_structured_action_fields(raw_res)
        policy_classification_summary = str(routing_reason or "").strip().lower() or None
        if backend_used == "ananta-worker" and self._native_worker_runtime_enabled(cfg):
            native_plan = get_native_worker_runtime_service().prepare_native_command_plan(
                tid=tid,
                task=task,
                command=command,
                reason=reason,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
                trace_id=str((session_payload or {}).get("id") or ""),
                context_bundle_id=worker_context_meta.get("context_bundle_id"),
                agent_cfg=cfg,
            )
            worker_context_meta.update(dict(native_plan.get("worker_context_updates") or {}))
            runtime_path = str(native_plan.get("runtime_path") or "").strip().lower()
            if runtime_path:
                routing["worker_runtime_path"] = runtime_path
            policy_classification_summary = str(native_plan.get("policy_classification_summary") or policy_classification_summary or "").strip().lower() or None
            if policy_classification_summary:
                routing["policy_classification_summary"] = policy_classification_summary
        append_stage(
            pipeline,
            name="parse",
            status="ok",
            metadata={"has_command": bool(command), "tool_call_count": len(tool_calls or [])},
        )
        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=task_kind,
            backend=backend_used,
            requested_backend="auto",
            routing_reason=routing_reason,
            policy_version=policy_version,
            metadata={**worker_context_meta, "source": "task_propose"},
        )
        pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
        response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=reason,
            raw=raw_res,
            backend=backend_used,
            model=proposal_model,
            routing=routing,
            cli_result={
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "repair_attempted": bool(repair_meta["attempted"]),
                "repair_backend": repair_meta["backend"],
                "repair_model": repair_meta["model"],
                "llm_call_profile": self._build_llm_call_profile_entries(
                    backend_used=backend_used,
                    model=proposal_model,
                    prompt=prompt_for_cli,
                    raw_output=raw_res,
                    latency_ms=latency_ms,
                    rc=rc,
                    repair_attempted=bool(repair_meta["attempted"]),
                    repair_backend=repair_meta["backend"],
                    repair_model=repair_meta["model"],
                ),
            },
            worker_context=worker_context_meta,
            trace=trace,
            review=self._build_review_state(
                current_app.config.get("AGENT_CONFIG", {}) or {},
                backend_used,
                task_kind,
                command=command,
                tool_calls=tool_calls,
            ),
            pipeline=pipeline_payload,
            command=command,
            tool_calls=tool_calls,
            history_event={
                "event_type": "proposal_result",
                "reason": reason,
                "backend": backend_used,
                "routing_reason": routing_reason,
                "latency_ms": latency_ms,
                "returncode": rc,
                "pipeline": pipeline_payload,
                "trace": trace,
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(trace.get("trace_id") or ""),
                    phase="propose",
                    propose_ok=True,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=policy_classification_summary,
                ),
            },
        )
        if session_payload:
            turn = get_cli_session_service().append_turn(
                session_id=session_payload["id"],
                prompt=prompt_for_cli,
                output=raw_res,
                model=proposal_model,
                trace_id=str(trace.get("trace_id") or ""),
                metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "command"},
            )
            if isinstance(turn, dict):
                response_payload.setdefault("routing", {})
                response_payload["routing"]["session_turn_id"] = turn.get("id")
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt_for_cli, task_id=tid)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
        return TaskScopedRouteResponse(data=response_payload)

    def _finalize_interactive_terminal_execution(
        self,
        *,
        tid: str,
        task: dict,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
        changed_files = get_worker_workspace_service().detect_changed_files_against_interactive_baseline(
            workspace_dir=workspace_ctx.workspace_dir
        )
        meaningful_changed_files = get_worker_workspace_service().filter_meaningful_changed_files(changed_files)
        workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        diff_artifact_ref = get_worker_workspace_service().create_workspace_diff_artifact(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        artifact_refs = list(workspace_artifact_refs or [])
        if diff_artifact_ref:
            artifact_refs.append(diff_artifact_ref)
        proposal_meta = dict(task.get("last_proposal") or {})
        cli_result = proposal_meta.get("cli_result") if isinstance(proposal_meta.get("cli_result"), dict) else {}
        exit_code = int(cli_result.get("returncode") or 0)
        status = "completed" if exit_code == 0 else "failed"
        output_lines = [
            "Interactive OpenCode session finalized.",
            f"Workspace: {workspace_ctx.workspace_dir}",
            f"Changed files: {len(changed_files)}",
        ]
        if changed_files:
            output_lines.extend(f"- {rel}" for rel in changed_files[:50])
        else:
            output_lines.append("No tracked workspace changes detected.")
        if diff_artifact_ref:
            output_lines.append(f"Diff artifact: {diff_artifact_ref.get('artifact_id')}")
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            backend=proposal_meta.get("backend"),
            requested_backend=proposal_meta.get("backend"),
            routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={
                "interactive_terminal_finalize": True,
                "changed_file_count": len(changed_files),
                "meaningful_changed_file_count": len(meaningful_changed_files),
                "workspace_artifact_count": len(artifact_refs),
            },
        )
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={"task_id": tid, "interactive_terminal_finalize": True},
        )
        append_stage(
            pipeline,
            name="interactive_terminal_finalize",
            status="ok" if exit_code == 0 else "error",
            metadata={
                "changed_file_count": len(changed_files),
                "meaningful_changed_file_count": len(meaningful_changed_files),
                "workspace_artifact_count": len(artifact_refs),
                "exit_code": exit_code,
            },
        )
        output = "\n".join(output_lines)
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=status,
            reason=reason or "Interactive OpenCode session finalized",
            command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
            tool_calls=None,
            output=output,
            exit_code=exit_code,
            retries_used=0,
            retry_history=[],
            failure_type="success" if exit_code == 0 else "command_failure",
            execution_duration_ms=int(cli_result.get("latency_ms") or 0),
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            artifact_refs=artifact_refs or None,
            extra_history={
                "workspace_changed_files": changed_files,
                "workspace_meaningful_changed_files": meaningful_changed_files,
                "workspace_dir": str(workspace_ctx.workspace_dir),
                "workspace_artifact_count": len(artifact_refs),
                "interactive_terminal_finalize": True,
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(((proposal_meta.get("trace") or {}).get("trace_id") or "")),
                    phase="execute",
                    propose_ok=True,
                    execute_ok=status == "completed",
                    artifact_created=bool(meaningful_changed_files),
                    worker_profile=((proposal_meta.get("worker_context") or {}).get("worker_profile") or (proposal_meta.get("routing") or {}).get("worker_profile")),
                    profile_source=((proposal_meta.get("worker_context") or {}).get("profile_source") or (proposal_meta.get("routing") or {}).get("profile_source")),
                    policy_classification=str(((proposal_meta.get("routing") or {}).get("policy_classification_summary") or (proposal_meta.get("routing") or {}).get("reason") or "")),
                ),
            },
        )
        get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=workspace_ctx.workspace_dir)
        return TaskScopedRouteResponse(data=response_payload)

    def _execute_research_artifact(
        self,
        *,
        tid: str,
        task: dict,
        proposal: dict,
        research_artifact: dict,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        review = (proposal.get("review") or {}) if isinstance(proposal, dict) else {}
        if review.get("required") and review.get("status") != "approved":
            raise TaskConflictError("research_review_required", details={"review": review, "task_id": tid})
        verification = self._verify_research_artifact(research_artifact)
        research_artifact["verification"] = verification
        if not verification.get("passed"):
            critique = get_execution_improvement_loop_service().build_verification_critique(
                expected_artifacts=[],
                verification=verification,
                observed_artifacts=[],
                logs=str(research_artifact.get("report_markdown") or ""),
            )
            raise TaskConflictError(
                "research_artifact_verification_failed",
                details={"verification": verification, "task_id": tid, "verification_critique": critique},
            )
        output = str(research_artifact.get("report_markdown") or "")
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((proposal.get("routing") or {}).get("task_kind")),
            policy_version=((proposal.get("trace") or {}).get("policy_version")),
            metadata={"task_id": tid, "artifact_execute": True},
        )
        append_stage(pipeline, name="artifact_finalize", status="ok", metadata={"artifact_kind": research_artifact.get("kind")})
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal.get("routing") or {}).get("task_kind")),
            backend=proposal.get("backend"),
            requested_backend=proposal.get("backend"),
            routing_reason=((proposal.get("routing") or {}).get("reason")),
            policy_version=((proposal.get("trace") or {}).get("policy_version")),
            metadata={"source": "research_artifact_execute", "artifact_kind": research_artifact.get("kind")},
        )
        trace["metadata"]["research_verification"] = verification
        artifact_ref = get_core_services().task_execution_tracking_service.persist_research_artifact(
            tid=tid,
            task=task,
            research_artifact=research_artifact,
        )
        verification_record = get_verification_service().create_or_update_record(
            tid,
            trace_id=trace.get("trace_id"),
            output=output,
            exit_code=0,
            gate_results=verification,
        )
        if isinstance(artifact_ref, dict):
            artifact_ref["verification_record_id"] = getattr(verification_record, "id", None)
            research_artifact.setdefault("trace", {})
            research_artifact["trace"]["persisted_artifact"] = artifact_ref
        from agent.metrics import TASK_COMPLETED

        TASK_COMPLETED.inc()
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status="completed",
            reason=proposal.get("reason", "Research report persisted"),
            command=None,
            tool_calls=None,
            output=output,
            exit_code=0,
            retries_used=0,
            retry_history=[],
            failure_type="success",
            execution_duration_ms=0,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            review=review,
            artifact_refs=[artifact_ref] if artifact_ref else None,
            extra_history={
                "artifact_kind": research_artifact.get("kind"),
                "artifact_ref": artifact_ref,
                "source_count": len(research_artifact.get("sources") or []),
                "verification": verification,
                "verification_record_id": getattr(verification_record, "id", None),
                "flow_metrics": self._build_flow_metrics_payload(
                    run_id=str(((proposal.get("trace") or {}).get("trace_id") or "")),
                    phase="execute",
                    propose_ok=True,
                    execute_ok=True,
                    artifact_created=bool(artifact_ref),
                    worker_profile=((proposal.get("worker_context") or {}).get("worker_profile") or (proposal.get("routing") or {}).get("worker_profile")),
                    profile_source=((proposal.get("worker_context") or {}).get("profile_source") or (proposal.get("routing") or {}).get("profile_source")),
                    policy_classification=str(((proposal.get("routing") or {}).get("policy_classification_summary") or (proposal.get("routing") or {}).get("reason") or "")),
                ),
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

    # --- cluster: forwarding_hub (remote forward, persist proposal/execution) ---
    # SPLIT-001f: all four methods delegate to agent.services._task_scoped_forwarding.
    def _forward_task_request_if_remote(
        self,
        *,
        tid: str,
        task: dict,
        endpoint: str,
        payload: dict,
        forwarder: Callable,
        on_success: Callable[[dict, dict], None],
    ) -> TaskScopedRouteResponse | None:
        from agent.services._task_scoped_forwarding import forward_task_request_if_remote
        return forward_task_request_if_remote(
            tid=tid, task=task, endpoint=endpoint, payload=payload,
            forwarder=forwarder, on_success=on_success,
        )

    def _persist_forwarded_proposal(self, response: dict, task: dict, request_payload: dict | None = None) -> None:
        from agent.services._task_scoped_forwarding import persist_forwarded_proposal
        return persist_forwarded_proposal(
            response, task, request_payload,
            allow_synthetic_llm_profile_fallback=self._allow_synthetic_llm_profile_fallback,
        )

    def _persist_forwarded_execution(self, *, tid: str, response: dict, task: dict, request_data) -> None:
        from agent.services._task_scoped_forwarding import persist_forwarded_execution
        return persist_forwarded_execution(tid=tid, response=response, task=task, request_data=request_data)

    @staticmethod
    def _normalize_forwarded_artifacts(*, task_id: str, artifacts: list[dict] | None) -> list[dict] | None:
        from agent.services._task_scoped_forwarding import normalize_forwarded_artifacts
        return normalize_forwarded_artifacts(task_id=task_id, artifacts=artifacts)

    # ── HF-T019: Hermes propose path ─────────────────────────────────────────

    # --- cluster: adapters (hermes, handler) ---
    # SPLIT-001g: all five methods delegate to agent.services._task_scoped_adapters.
    def _try_hermes_propose(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        research_context: object,
        cfg: dict,
    ) -> TaskScopedRouteResponse | None:
        from agent.services._task_scoped_adapters import try_hermes_propose
        return try_hermes_propose(tid=tid, task=task, task_kind=task_kind, request_data=request_data,
                                   research_context=research_context, cfg=cfg)

    def _invoke_hermes_adapter(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        research_context: object,
        hermes_cfg_raw: dict,
    ) -> dict | None:
        from agent.services._task_scoped_adapters import invoke_hermes_adapter
        return invoke_hermes_adapter(tid=tid, task=task, task_kind=task_kind, request_data=request_data,
                                     research_context=research_context, hermes_cfg_raw=hermes_cfg_raw)

    def _try_handler_propose(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        base_prompt: str,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse | None:
        from agent.services._task_scoped_adapters import try_handler_propose
        return try_handler_propose(
            tid=tid, task=task, task_kind=task_kind, request_data=request_data,
            base_prompt=base_prompt, cli_runner=cli_runner, forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
            service=self, build_review_state=self._build_review_state,
        )

    def _try_handler_execute(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        forwarder: Callable,
    ) -> TaskScopedRouteResponse | None:
        from agent.services._task_scoped_adapters import try_handler_execute
        return try_handler_execute(tid=tid, task=task, task_kind=task_kind,
                                   request_data=request_data, forwarder=forwarder, service=self)

    def _coerce_handler_response(self, response: object | None) -> TaskScopedRouteResponse | None:
        from agent.services._task_scoped_adapters import coerce_handler_response
        return coerce_handler_response(response)

    def _require_task(self, tid: str) -> dict:
        task = get_local_task_status(tid)
        if not task:
            task = self._maybe_sync_task_from_hub(tid)
        if not task:
            raise TaskNotFoundError()
        return task

    def _maybe_sync_task_from_hub(self, tid: str) -> dict | None:
        try:
            agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if current_app else {}
        except Exception:
            agent_cfg = {}
        fp = dict((agent_cfg.get("execution_fallback_policy") or {}))
        if not bool(fp.get("worker_task_sync_from_hub_enabled", True)):
            return None
        from agent.services.task_runtime_service import sync_task_from_hub
        return sync_task_from_hub(tid)

    # --- cluster: runtime (cli backend, opencode session, interactive terminal, research) ---
    def _resolve_cli_backend(
        self,
        task_kind: str,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        required_capabilities: list[str] | None = None,
    ) -> tuple[str, str]:
        backend, reason, _ = resolve_cli_backend(
            task_kind=task_kind,
            requested_backend=requested_backend,
            supported_backends=SUPPORTED_CLI_BACKENDS,
            agent_cfg=agent_cfg if agent_cfg is not None else (current_app.config.get("AGENT_CONFIG", {}) or {}),
            fallback_backend="sgpt",
            required_capabilities=required_capabilities,
        )
        return backend, reason

    @staticmethod
    def _coalesce_cli_output(stdout: str | None, stderr: str | None) -> tuple[str, str]:
        out = str(stdout or "").strip()
        if out:
            return out, "stdout"
        err = str(stderr or "").strip()
        if err:
            return err, "stderr"
        return "", "none"

    @classmethod
    def _sanitize_structured_output_text(cls, raw_text: str) -> str:
        return sanitize_structured_output_text(raw_text)

    @staticmethod
    def _normalize_tool_calls(tool_calls: object) -> list[dict] | None:
        if isinstance(tool_calls, list) and all(isinstance(item, dict) for item in tool_calls):
            return tool_calls
        if isinstance(tool_calls, dict):
            return [tool_calls]
        return None

    @classmethod
    def _normalize_structured_action_payload(cls, data: object) -> dict | None:
        return normalize_structured_action_payload(data)

    @classmethod
    # --- cluster: repair (structured-action parse, repair, shell-meta-block) ---
    def _parse_structured_action_payload(cls, raw_text: str) -> dict | None:
        return parse_structured_action_payload(raw_text)

    @classmethod
    def _locally_repair_structured_action_output(cls, raw_text: str) -> str | None:
        return locally_repair_structured_action_output(raw_text)

    @classmethod
    def _extract_structured_action_fields(cls, raw_text: str) -> tuple[str | None, list[dict] | None]:
        return extract_structured_action_fields(raw_text)

    def _repair_task_proposal(
        self,
        *,
        cli_runner: Callable,
        prompt: str,
        bad_output: str,
        validation_error: str,
        timeout: int,
        task_kind: str,
        policy_version: str,
        cfg: dict,
        primary_backend: str,
        primary_model: str | None,
        primary_temperature: float | None = None,
        research_context: dict | None = None,
        session: dict | None = None,
        workdir: str | None = None,
    ) -> dict | None:
        from agent.services._task_scoped_repair import repair_task_proposal
        return repair_task_proposal(
            cli_runner=cli_runner,
            prompt=prompt,
            bad_output=bad_output,
            validation_error=validation_error,
            timeout=timeout,
            task_kind=task_kind,
            policy_version=policy_version,
            cfg=cfg,
            primary_backend=primary_backend,
            primary_model=primary_model,
            primary_temperature=primary_temperature,
            research_context=research_context,
            session=session,
            workdir=workdir,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
            normalize_temperature=self._normalize_temperature,
        )

    @staticmethod
    def _is_timeout_like_repair_failure(*, validation_error: str, bad_output: str) -> bool:
        from agent.services._task_scoped_repair import is_timeout_like_repair_failure
        return is_timeout_like_repair_failure(validation_error=validation_error, bad_output=bad_output)

    @staticmethod
    def _is_shell_meta_blocked_failure(output: str | None, failure_type: str | None) -> bool:
        from agent.services._task_scoped_repair import is_shell_meta_blocked_failure
        return is_shell_meta_blocked_failure(output, failure_type)

    @staticmethod
    def _is_command_not_found_failure(output: str | None, failure_type: str | None) -> bool:
        from agent.services._task_scoped_repair import is_command_not_found_failure
        return is_command_not_found_failure(output, failure_type)

    @staticmethod
    def _estimate_tokens(value: str | None) -> int:
        from agent.services._task_scoped_repair import estimate_tokens
        return estimate_tokens(value)

    def _build_llm_call_profile_entries(
        self,
        *,
        backend_used: str,
        model: str | None,
        prompt: str,
        raw_output: str,
        latency_ms: int,
        rc: int,
        repair_attempted: bool,
        repair_backend: str | None,
        repair_model: str | None,
    ) -> list[dict]:
        from agent.services._task_scoped_repair import build_llm_call_profile_entries
        return build_llm_call_profile_entries(
            backend_used=backend_used,
            model=model,
            prompt=prompt,
            raw_output=raw_output,
            latency_ms=latency_ms,
            rc=rc,
            repair_attempted=repair_attempted,
            repair_backend=repair_backend,
            repair_model=repair_model,
        )

    @staticmethod
    def _build_repair_prompt(*, prompt: str, bad_output: str, validation_error: str) -> str:
        from agent.services._task_scoped_repair import build_repair_prompt
        return build_repair_prompt(prompt=prompt, bad_output=bad_output, validation_error=validation_error)

    def _attempt_repaired_execute_after_meta_block(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        command: str | None,
        execution_output: str | None,
        execution_policy,
        agent_cfg: dict,
        cli_runner: Callable,
        tool_definitions_resolver: Callable | None,
        pipeline: dict,
        workspace_dir: str,
        exec_started_at: float | None,
    ) -> dict | None:
        from agent.services._task_scoped_repair import attempt_repaired_execute_after_meta_block
        return attempt_repaired_execute_after_meta_block(
            tid=tid,
            task=task,
            task_kind=task_kind,
            command=command,
            execution_output=execution_output,
            execution_policy=execution_policy,
            agent_cfg=agent_cfg,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            pipeline=pipeline,
            workspace_dir=workspace_dir,
            exec_started_at=exec_started_at,
            build_task_propose_prompt=self._build_task_propose_prompt,
            resolve_task_propose_timeout=self._resolve_task_propose_timeout,
            resolve_requested_model=self._resolve_requested_model,
            normalize_temperature=self._normalize_temperature,
            prepare_task_cli_session=self._prepare_task_cli_session,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
        )

    def _build_research_result(
        self,
        raw_res: str,
        backend_used: str,
        tid: str | None,
        rc: int,
        cli_err: str,
        latency_ms: int,
        output_source: str = "stdout",
        research_context: dict | None = None,
    ) -> dict:
        artifact = normalize_research_artifact(
            raw_res,
            backend=backend_used,
            task_id=tid,
            cli_result={
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
            },
            research_context=research_context,
        )
        return {
            "reason": artifact.get("summary") or "Research report generated",
            "raw": raw_res,
            "research_artifact": artifact,
            "research_context": research_context,
            "backend": backend_used,
            "command": None,
            "tool_calls": None,
            "cli_result": {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "llm_call_profile": self._build_llm_call_profile_entries(
                    backend_used=backend_used,
                    model=artifact.get("model"),
                    prompt=(research_context or {}).get("prompt_section"),
                    raw_output=raw_res,
                    latency_ms=latency_ms,
                    rc=rc,
                    repair_attempted=False,
                    repair_backend=None,
                    repair_model=None,
                ),
            },
        }

    def _verify_research_artifact(self, research_artifact: dict | None) -> dict:
        artifact = dict(research_artifact or {})
        report_markdown = str(artifact.get("report_markdown") or "").strip()
        sources = list(artifact.get("sources") or [])
        citations = list(artifact.get("citations") or [])
        passed = bool(report_markdown and sources)
        verification = {
            "passed": passed,
            "ready": passed,
            "has_report": bool(report_markdown),
            "has_sources": bool(sources),
            "has_citations": bool(citations),
            "source_count": len(sources),
            "citation_count": len(citations),
            "reason": "verified" if passed else "missing_sources_or_report",
        }
        artifact_verification = dict(artifact.get("verification") or {})
        artifact_verification.update(verification)
        artifact["verification"] = artifact_verification
        return artifact_verification

    def _build_review_state(
        self,
        agent_cfg: dict,
        backend: str,
        task_kind: str,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
    ) -> dict:
        risk_level = max_risk_level(
            classify_command_risk(command),
            classify_tool_calls_risk(tool_calls, guard_cfg=agent_cfg),
        )
        policy = review_policy(
            agent_cfg,
            backend=backend,
            task_kind=task_kind,
            risk_level=risk_level,
            uses_terminal=has_terminal_signal(command),
            uses_file_access=has_file_access_signal(command, tool_calls),
        )
        return {
            "required": bool(policy.get("required")),
            "status": "pending" if policy.get("required") else "not_required",
            "policy_version": policy.get("policy_version"),
            "reason": policy.get("reason"),
            "risk_level": policy.get("risk_level"),
            "uses_terminal": policy.get("uses_terminal"),
            "uses_file_access": policy.get("uses_file_access"),
            "reviewed_by": None,
            "reviewed_at": None,
            "comment": None,
        }

    def _get_worker_execution_context(
        self,
        task: dict | None,
        *,
        tid: str | None = None,
        base_prompt: str | None = None,
    ) -> dict:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
        semantic_policy = self._resolve_worker_semantic_output_correction_policy(agent_cfg)
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        if execution_context:
            execution_context["allowed_tools"] = normalize_allowed_tools(execution_context.get("allowed_tools"))
            if semantic_policy and not isinstance(execution_context.get("semantic_output_correction"), dict):
                execution_context["semantic_output_correction"] = semantic_policy
            profile, profile_source = resolve_worker_execution_profile(
                worker_execution_context=execution_context,
                agent_cfg=agent_cfg,
            )
            execution_context["worker_profile"] = profile
            execution_context["profile_source"] = profile_source
            auto_bundle_cfg = dict(
                (agent_cfg.get("worker_runtime") or {}).get("codecompass_auto_bundle") or {}
            )
            if auto_bundle_cfg.get("enabled") and not list(
                (execution_context.get("context") or {}).get("chunks") or []
            ):
                kind_filter = [
                    str(k).strip().lower()
                    for k in list(auto_bundle_cfg.get("task_kinds") or [])
                    if str(k).strip()
                ]
                routing_kind = str(
                    (execution_context.get("routing_hints") or {}).get("task_kind") or ""
                ).strip().lower()
                if not kind_filter or not routing_kind or routing_kind in kind_filter:
                    try:
                        resolved = get_context_manager_service().ensure_task_context_bundle(
                            task=dict(task or {}),
                            task_id=tid,
                            query=base_prompt,
                        )
                        bundle = resolved.get("context_bundle")
                        if bundle:
                            ctx = dict(execution_context.get("context") or {})
                            ctx.setdefault("chunks", []).extend(list(bundle.chunks or []))
                            ctx["token_estimate"] = (
                                int(ctx.get("token_estimate") or 0)
                                + int(bundle.token_estimate or 0)
                            )
                            if not ctx.get("context_text") and bundle.context_text:
                                ctx["context_text"] = bundle.context_text
                            execution_context["context"] = ctx
                            execution_context.setdefault("context_bundle_id", bundle.id)
                    except Exception:
                        pass
            return execution_context
        bundle_id = str((task or {}).get("context_bundle_id") or "").strip()
        bundle = None
        if bundle_id:
            bundle = get_repository_registry().context_bundle_repo.get_by_id(bundle_id)
        if bundle is None and (tid or (task or {})):
            resolved = get_context_manager_service().ensure_task_context_bundle(
                task=dict(task or {}),
                task_id=tid,
                query=base_prompt,
            )
            resolved_bundle = resolved.get("context_bundle")
            if resolved_bundle is not None:
                bundle = resolved_bundle
        if bundle is None:
            return {}
        profile, profile_source = resolve_worker_execution_profile(
            worker_execution_context={},
            agent_cfg=agent_cfg,
        )
        resolved_context = {
            "context_bundle_id": bundle.id,
            "worker_profile": profile,
            "profile_source": profile_source,
            "context": {
                "context_text": bundle.context_text,
                "chunks": list(bundle.chunks or []),
                "token_estimate": int(bundle.token_estimate or 0),
                "bundle_metadata": dict(bundle.bundle_metadata or {}),
            },
        }
        if semantic_policy:
            resolved_context["semantic_output_correction"] = semantic_policy
        return resolved_context

    def _tool_definitions_for_task(
        self,
        task: dict | None,
        *,
        tool_definitions_resolver: Callable,
        execution_context: dict | None = None,
    ) -> list[dict]:
        execution_context = dict(execution_context or self._get_worker_execution_context(task))
        allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
        if allowed_tools:
            return tool_definitions_resolver(allowlist=allowed_tools)
        return tool_definitions_resolver()

    @staticmethod
    def _cli_session_policy(agent_cfg: dict | None) -> dict:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_cli_session_policy
        from agent.services._task_scoped_config_policy import resolve_cli_session_policy
        return resolve_cli_session_policy(agent_cfg)

    @staticmethod
    def _resolve_opencode_execution_mode(agent_cfg: dict | None) -> str:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_opencode_execution_mode
        from agent.services._task_scoped_config_policy import (
            resolve_opencode_execution_mode,
        )
        return resolve_opencode_execution_mode(agent_cfg)

    @staticmethod
    def _resolve_opencode_interactive_launch_mode(agent_cfg: dict | None) -> str:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_opencode_interactive_launch_mode
        from agent.services._task_scoped_config_policy import (
            resolve_opencode_interactive_launch_mode,
        )
        return resolve_opencode_interactive_launch_mode(agent_cfg)

    @staticmethod
    def _native_worker_runtime_enabled(agent_cfg: dict | None) -> bool:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.native_worker_runtime_enabled
        from agent.services._task_scoped_config_policy import (
            native_worker_runtime_enabled,
        )
        return native_worker_runtime_enabled(agent_cfg)

    def _should_use_native_worker_runtime(self, *, proposal_meta: dict | None, agent_cfg: dict | None, command: str | None) -> bool:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.should_use_native_worker_runtime
        from agent.services._task_scoped_config_policy import (
            should_use_native_worker_runtime,
        )
        return should_use_native_worker_runtime(
            proposal_meta=proposal_meta,
            agent_cfg=agent_cfg,
            command=command,
        )

    def _resolve_task_role_identity(self, tid: str, task: dict) -> tuple[str | None, str | None]:
        task_record = get_repository_registry().task_repo.get_by_id(tid)
        if not task_record:
            return None, None
        role_id = getattr(task_record, "assigned_role_id", None)
        if task_record.team_id and task_record.assigned_agent_url:
            members = get_repository_registry().team_member_repo.get_by_team(task_record.team_id)
            for member in members:
                if member.agent_url == task_record.assigned_agent_url and not role_id:
                    role_id = member.role_id
                    break
        role_name = None
        if role_id:
            role = get_repository_registry().role_repo.get_by_id(role_id)
            if role:
                role_name = role.name
        return str(role_id or "").strip() or None, str(role_name or "").strip() or None

    def _resolve_task_session_scope(self, *, tid: str, task: dict, policy: dict) -> tuple[str, str, str | None]:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        workspace = dict(execution_context.get("workspace") or {})
        explicit_scope_key = str(workspace.get("session_scope_key") or "").strip()
        if explicit_scope_key:
            explicit_scope_kind = str(workspace.get("session_scope_kind") or "workspace").strip().lower() or "workspace"
            return explicit_scope_kind, explicit_scope_key, None

        reuse_scope = str(policy.get("reuse_scope") or "task").strip().lower()
        if reuse_scope == "role":
            role_id, role_name = self._resolve_task_role_identity(tid, task)
            if role_id or role_name:
                role_key = role_id or f"role-name:{role_name}"
                return "role", str(role_key), role_name
        return "task", f"task:{tid}", None

    @staticmethod
    def _has_native_opencode_runtime(session_payload: dict | None) -> bool:
        # SPLIT-001d: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.has_native_opencode_runtime
        from agent.services._task_scoped_config_policy import (
            has_native_opencode_runtime,
        )
        return has_native_opencode_runtime(session_payload)

    def _prepare_task_cli_session(
        self,
        *,
        tid: str,
        task: dict,
        backend: str,
        model: str | None,
        agent_cfg: dict | None,
    ) -> dict | None:
        policy = self._cli_session_policy(agent_cfg)
        backend_name = str(backend or "").strip().lower()
        opencode_execution_mode = self._resolve_opencode_execution_mode(agent_cfg)
        opencode_interactive_launch_mode = self._resolve_opencode_interactive_launch_mode(agent_cfg)
        terminal_execution_mode = (
            opencode_execution_mode if backend_name == "opencode" and opencode_execution_mode in {"live_terminal", "interactive_terminal"} else None
        )
        if not terminal_execution_mode and (not policy["enabled"] or not policy["allow_task_scoped_auto_session"]):
            return None
        if not terminal_execution_mode and backend_name not in set(policy["stateful_backends"]):
            return None
        scope_kind, scope_key, role_name = self._resolve_task_session_scope(tid=tid, task=task, policy=policy)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        workspace_dir = str(workspace_context.workspace_dir)
        verification = dict(task.get("verification_status") or {})
        session_meta = verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {}
        existing_id = str(session_meta.get("session_id") or "").strip()
        session = get_cli_session_service().get_session(existing_id, include_history=False) if existing_id else None
        if (
            not session
            or str(session.get("status") or "").strip().lower() != "active"
            or str(session.get("backend") or "").strip().lower() != backend_name
        ):
            session = get_cli_session_service().find_active_session(
                backend=backend_name,
                scope_key=scope_key,
                scope_kind=scope_kind,
            )
        session_reused = False
        if session and str(session.get("status") or "").strip().lower() == "active" and str(session.get("backend") or "").strip().lower() == backend_name:
            session_payload = dict(session)
            session_reused = True
        else:
            session_payload = get_cli_session_service().create_session(
                backend=backend_name,
                model=model,
                metadata={
                    "source": "task_propose_auto_session",
                    "task_id": tid,
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                    "role_name": role_name,
                    "opencode_workdir": workspace_dir,
                    "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                },
                task_id=tid,
                conversation_id=scope_key,
            )
            verification["cli_session"] = {
                "session_id": session_payload.get("id"),
                "backend": backend_name,
                "model": model,
                "status": "active",
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "updated_at": time.time(),
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        if backend_name == "opencode" and not terminal_execution_mode:
            session_payload = (
                get_cli_session_service().update_session(
                    str(session_payload.get("id") or ""),
                    metadata_updates={
                        "opencode_execution_mode": "backend",
                        "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                        "opencode_live_terminal": {},
                    },
                )
                or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "execution_mode": "backend",
                "terminal_session_id": None,
                "forward_param": None,
                "terminal_status": None,
                "updated_at": time.time(),
            }
            verification["opencode_live_terminal"] = {}
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        if terminal_execution_mode:
            terminal_meta = (
                get_live_terminal_session_service().ensure_session_for_cli(
                    session_payload,
                    execution_mode=terminal_execution_mode,
                    workdir=workspace_dir,
                )
                or {}
            )
            interactive_terminal_workspace = (
                dict(verification.get("interactive_terminal_workspace") or {})
                if isinstance(verification.get("interactive_terminal_workspace"), dict)
                else {}
            )
            if terminal_execution_mode == "interactive_terminal" and not interactive_terminal_workspace.get("baseline_ready"):
                baseline_meta = get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=Path(workspace_dir))
                interactive_terminal_workspace = {
                    "baseline_ready": True,
                    **baseline_meta,
                }
            session_payload = (
                get_cli_session_service().update_session(
                    str(session_payload.get("id") or ""),
                    metadata_updates={
                        "opencode_execution_mode": terminal_execution_mode,
                        "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                        "opencode_live_terminal": terminal_meta,
                        "opencode_workdir": workspace_dir,
                    },
                )
                or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "execution_mode": terminal_execution_mode,
                "terminal_session_id": terminal_meta.get("terminal_session_id"),
                "forward_param": terminal_meta.get("forward_param"),
                "agent_url": terminal_meta.get("agent_url"),
                "agent_name": terminal_meta.get("agent_name"),
                "terminal_status": terminal_meta.get("status"),
                "updated_at": time.time(),
            }
            if interactive_terminal_workspace:
                verification["interactive_terminal_workspace"] = interactive_terminal_workspace
            verification["opencode_live_terminal"] = dict(terminal_meta)
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        elif backend_name == "opencode" and bool(policy.get("native_opencode_sessions")):
            from agent.services.opencode_runtime_service import get_opencode_runtime_service

            runtime_meta = get_opencode_runtime_service().ensure_session_runtime(session_payload, model=model)
            session_payload = (
                get_cli_session_service().get_session(str(session_payload.get("id") or ""), include_history=False) or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "native_session_id": runtime_meta.get("native_session_id"),
                "server_key": runtime_meta.get("server_key"),
                "server_url": runtime_meta.get("server_url"),
                "agent": runtime_meta.get("agent"),
                "updated_at": time.time(),
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        get_cli_session_service().prune_sessions(max_sessions=policy["max_sessions"])
        session_payload["session_reused"] = bool(session_reused)
        session_payload["max_turns_per_session"] = policy["max_turns_per_session"]
        return session_payload

    def _build_task_propose_prompt(
        self,
        *,
        tid: str,
        task: dict,
        base_prompt: str,
        tool_definitions_resolver: Callable,
        research_context: dict | None = None,
        interactive_terminal: bool = False,
        context_profile: dict | None = None,
    ) -> tuple[str, dict]:
        execution_context = self._get_worker_execution_context(task, tid=tid, base_prompt=base_prompt)
        context_payload = dict(execution_context.get("context") or {})
        retrieval_trace_link = self._extract_retrieval_trace_link(context_payload)
        context_text = str(context_payload.get("context_text") or "").strip()
        context_profile_payload = dict(context_profile or {})
        compact_profile = bool(context_profile_payload.get("compact"))
        task_brief_char_limit = (
            self._bounded_int(context_profile_payload.get("task_brief_char_limit"), default=900, minimum=180, maximum=4000)
            if compact_profile
            else None
        )
        hub_context_char_limit = (
            self._bounded_int(context_profile_payload.get("hub_context_char_limit"), default=2600, minimum=256, maximum=12000)
            if compact_profile
            else None
        )
        research_prompt_char_limit = (
            self._bounded_int(context_profile_payload.get("research_prompt_char_limit"), default=1800, minimum=200, maximum=12000)
            if compact_profile
            else None
        )
        if hub_context_char_limit and len(context_text) > hub_context_char_limit:
            context_text = context_text[: max(1, hub_context_char_limit - 14)].rstrip() + "\n\n[gekürzt]"
        workspace_payload = dict(execution_context.get("workspace") or {})
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
        expected_output_schema = dict(execution_context.get("expected_output_schema") or {})
        semantic_output_correction = (
            dict(execution_context.get("semantic_output_correction") or {})
            if isinstance(execution_context.get("semantic_output_correction"), dict)
            else {}
        )
        worker_profile = normalize_worker_execution_profile(execution_context.get("worker_profile"))
        profile_source = str(execution_context.get("profile_source") or "agent_default").strip().lower() or "agent_default"
        tool_definitions = self._tool_definitions_for_task(
            task,
            tool_definitions_resolver=tool_definitions_resolver,
            execution_context=execution_context,
        )

        prompt_sections: list[str] = []
        system_prompt = self._get_system_prompt_for_task(tid)
        instruction_stack = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            emit_audit=True,
        )
        effective_system_prompt = str(instruction_stack.get("rendered_system_prompt") or "").strip() or None
        stack_diagnostics = dict(instruction_stack.get("diagnostics") or {})
        shell_command_mode = str(execution_context.get("shell_command_mode") or "").strip().lower()
        allow_complex_shell = shell_command_mode == "pipeline"
        _raw_pattern_hints = execution_context.get("pattern_hints_normalized")
        _pattern_hints = dict(_raw_pattern_hints) if isinstance(_raw_pattern_hints, dict) and _raw_pattern_hints else None
        opencode_context_files = get_worker_workspace_service().prepare_opencode_context_files(
            task=task,
            workspace_context=workspace_context,
            base_prompt=base_prompt,
            system_prompt=effective_system_prompt,
            context_text=context_text,
            expected_output_schema=expected_output_schema,
            tool_definitions=tool_definitions,
            research_context=research_context,
            include_response_contract=not interactive_terminal,
            allow_complex_shell=allow_complex_shell,
            task_brief_char_limit=task_brief_char_limit,
            context_text_char_limit=hub_context_char_limit,
            research_prompt_char_limit=research_prompt_char_limit,
            pattern_hints=_pattern_hints,
        )
        prompt_sections.append(f"Aktueller Auftrag: {base_prompt}")
        read_paths = [
            str(opencode_context_files.get("agents_path") or "").strip(),
            str(opencode_context_files.get("context_index_path") or "").strip(),
            str(opencode_context_files.get("task_brief_path") or "").strip(),
        ]
        if context_text:
            read_paths.append(str(opencode_context_files.get("hub_context_path") or ".ananta/hub-context.md"))
        if not interactive_terminal:
            read_paths.append(str(opencode_context_files.get("response_contract_path") or "").strip())
        read_paths = [item for item in read_paths if item]
        if read_paths:
            prompt_sections.append(
                "Lies zuerst die bereitgestellten Workspace-Dateien und verwende diesen Dateikontext "
                "statt lange Inhalte zu wiederholen:\n" + "\n".join(f"- {item}" for item in read_paths)
            )
        if context_text:
            prompt_sections.append(
                "Selektierter Hub-Kontext ist im Hub-Kontext enthalten und wird aus derselben Datei geladen. "
                "Selektierter Research-Kontext wird aus derselben Datei geladen."
            )
            context_preview = " ".join(str(context_text).split()).strip().lower()[:240]
            if context_preview and not compact_profile:
                prompt_sections.append(f"Kurzvorschau Hub-Kontext: {context_preview}")
        research_prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
        if research_prompt_section:
            prompt_sections.append(
                "Selektierter Research-Kontext ist ausgelagert in "
                f"{str(opencode_context_files.get('research_context_prompt_path') or 'rag_helper/research-context.md')}."
            )
            if not compact_profile:
                research_preview = " ".join(research_prompt_section.split()).strip().lower()[:320]
                if research_preview:
                    prompt_sections.append(f"Kurzvorschau Research-Kontext: {research_preview}")
        if allowed_tools:
            prompt_sections.append(
                "Tool-Scope fuer diesen Task (nur diese Tools verwenden): "
                + ", ".join(str(item) for item in allowed_tools)
            )
        prompt_sections.append(
            f"Worker-Ausfuehrungsprofil: {worker_profile} (source={profile_source})."
        )
        if expected_output_schema and not compact_profile:
            prompt_sections.append(
                "Erwartetes Output-Schema (Kurzfassung): "
                + json.dumps(expected_output_schema, ensure_ascii=False)[:400]
            )
        if stack_diagnostics and not compact_profile:
            prompt_sections.append(get_instruction_layer_service().render_diagnostics_brief(stack_diagnostics))
        prompt_sections.append(
            "Arbeitsverzeichnis fuer Datei-/Shell-Aktionen:\n"
            f"- workspace: {workspace_context.workspace_dir}\n"
            f"- artifacts: {workspace_context.artifacts_dir}\n"
            f"- rag_helper: {workspace_context.rag_helper_dir}\n"
            "Nutze ausschliesslich diesen Workspace fuer neue oder geaenderte Dateien."
        )
        if interactive_terminal:
            if compact_profile:
                prompt_sections.append(
                    "Kompaktmodus aktiv: nutze die Workspace-Dateien als Quelle der Wahrheit und vermeide Kontext-Wiederholung."
                )
            prompt_sections.append(
                "Arbeite direkt im Workspace mit normalem OpenCode-CLI. "
                "Fuehre die gewuenschten Datei- und Verzeichnis-Aenderungen im Workspace aus. "
                "Nutze bei Bedarf `rag_helper/` fuer Hilfsdateien oder ausgelagerten Kontext. "
                "Es ist keine JSON-Antwort erforderlich; Workspace-Aenderungen und Diffs werden nach dem Lauf automatisch erfasst."
            )
        else:
            prompt_sections.append(
                "Antworte ausschliesslich als genau ein JSON-Objekt. "
                "Beachte dafuer die Regeln in "
                f"{str(opencode_context_files.get('response_contract_path') or '.ananta/response-contract.md')} "
                "und setze mindestens eines von 'command' oder 'tool_calls'."
            )
            if allow_complex_shell:
                prompt_sections.append(
                    "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                    "Falls ein Shell-Befehl erforderlich ist, liefere einen `command` — "
                    "Pipes (`|`), Redirects (`>`, `<`, `2>&1`) und Chaining (`&&`, `||`, `;`) sind erlaubt."
                )
            else:
                prompt_sections.append(
                    "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                    "Falls ein Shell-Befehl erforderlich ist, liefere genau einen einzelnen `command` "
                    "ohne `&&`, `||`, `;`, `>`, `<` oder `|`."
                )
        return "\n\n".join(section for section in prompt_sections if section), {
            "context_bundle_id": execution_context.get("context_bundle_id") or task.get("context_bundle_id"),
            "allowed_tools": allowed_tools,
            "expected_output_schema": expected_output_schema,
            "semantic_output_correction": semantic_output_correction if semantic_output_correction else None,
            "worker_profile": worker_profile,
            "profile_source": profile_source,
            "workspace": {
                "requested": workspace_payload or None,
                "workspace_dir": str(workspace_context.workspace_dir),
                "artifacts_dir": str(workspace_context.artifacts_dir),
                "rag_helper_dir": str(workspace_context.rag_helper_dir),
                "opencode_context_files": opencode_context_files,
            },
            "context_chunk_count": len(context_payload.get("chunks") or []),
            "has_context_text": bool(context_text),
            "retrieval_trace_id": retrieval_trace_link["retrieval_trace_id"],
            "retrieval_context_hash": retrieval_trace_link["retrieval_context_hash"],
            "retrieval_manifest_hash": retrieval_trace_link["retrieval_manifest_hash"],
            "instruction_layers": stack_diagnostics,
            "research_context": {
                "artifact_ids": list((research_context or {}).get("artifact_ids") or []),
                "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or []),
                "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or []),
                "truncated": bool((research_context or {}).get("truncated")),
                "context_char_count": int((research_context or {}).get("context_char_count") or 0),
            }
            if research_context
            else None,
        }

    def _get_system_prompt_for_task(self, tid: str) -> str | None:
        task = get_repository_registry().task_repo.get_by_id(tid)
        if not task:
            return None

        repos = get_repository_registry()
        resolved = resolve_task_role_template(task, repos=repos)
        role_id = resolved.get("role_id")
        template_id = resolved.get("template_id")
        if not template_id:
            return None
        template = repos.template_repo.get_by_id(template_id)
        if not template:
            return None

        prompt = template.prompt_template
        goal_text = ""
        goal_context = ""
        acceptance_criteria: list[str] = []
        goal_id = str(task.goal_id or "").strip()
        if goal_id:
            goal = repos.goal_repo.get_by_id(goal_id)
            if goal:
                goal_text = str(goal.goal or "").strip()
                goal_context = str(goal.context or "").strip()
                acceptance_criteria = [str(item) for item in (goal.acceptance_criteria or []) if str(item or "").strip()]
        variables = {
            "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
            "task_title": task.title or "Kein Titel",
            "task_description": task.description or "Keine Beschreibung",
            "team_goal": goal_text
            or str(task.title or "").strip()
            or str(task.description or "").strip()
            or str(resolved.get("team_name") or "").strip()
            or "aktuelles Teamziel",
            "goal_context": goal_context,
            "acceptance_criteria": "\n".join(f"- {item}" for item in acceptance_criteria),
        }
        if resolved.get("team_name"):
            variables["team_name"] = resolved["team_name"]
        if resolved.get("role_name"):
            variables["role_name"] = resolved["role_name"]
        for key, value in variables.items():
            prompt = prompt.replace("{{" + key + "}}", str(value))
        return prompt

    @staticmethod
    def _routing_dimensions(
        *,
        backend_used: str,
        model: str | None,
        temperature: float | None = None,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        worker_profile: str | None = None,
        profile_source: str | None = None,
    ) -> dict:
        backend = str(backend_used or "").strip().lower()
        requested = str(requested_backend or "auto").strip().lower()
        normalized_profile = normalize_worker_execution_profile(worker_profile)
        normalized_profile_source = str(profile_source or "agent_default").strip().lower() or "agent_default"
        cfg = agent_cfg if isinstance(agent_cfg, dict) else ((current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {})
        runtime_cfg = cfg.get("worker_runtime") if isinstance(cfg.get("worker_runtime"), dict) else {}
        native_runtime_cfg = runtime_cfg.get("native_worker_runtime") if isinstance(runtime_cfg.get("native_worker_runtime"), dict) else {}
        runtime_path = None
        if backend == "ananta-worker":
            runtime_path = "native_worker_pipeline" if bool(native_runtime_cfg.get("enabled", False)) else "sgpt_fallback_proxy"
        dimensions = {
            "requested_backend": requested or "auto",
            "execution_backend": backend or requested or "sgpt",
            "inference_provider": None,
            "inference_model": str(model or "").strip() or None,
            "inference_temperature": TaskScopedExecutionService._normalize_temperature(temperature),
            "inference_base_url": None,
            "inference_target_kind": None,
            "inference_target_provider_type": None,
            "remote_hub": False,
            "instance_id": None,
            "max_hops": None,
            "worker_profile": normalized_profile,
            "profile_source": normalized_profile_source,
            "worker_runtime_path": runtime_path,
        }
        if backend == "codex":
            runtime_cfg = resolve_codex_runtime_config() if has_app_context() else {}
            dimensions.update(
                {
                    "inference_provider": runtime_cfg.get("target_provider") or str(cfg.get("default_provider") or "").strip().lower() or "openai_compatible",
                    "inference_base_url": runtime_cfg.get("base_url"),
                    "inference_target_kind": runtime_cfg.get("target_kind"),
                    "inference_target_provider_type": runtime_cfg.get("target_provider_type"),
                    "remote_hub": bool(runtime_cfg.get("remote_hub")),
                    "instance_id": runtime_cfg.get("instance_id"),
                    "max_hops": runtime_cfg.get("max_hops"),
                }
            )
            return dimensions
        dimensions["inference_provider"] = str(cfg.get("default_provider") or "").strip().lower() or None
        return dimensions

    @staticmethod
    def _terminal_parent_goal_guard(*, tid: str, task: dict, phase: str) -> TaskScopedRouteResponse | None:
        goal_id = str((task or {}).get("goal_id") or "").strip()
        if not goal_id:
            return None
        goal = get_repository_registry().goal_repo.get_by_id(goal_id)
        goal_status = str(getattr(goal, "status", "") or "").strip().lower() if goal is not None else ""
        if goal_status not in {"completed", "failed", "cancelled", "aborted", "timeout"}:
            return None
        update_local_task_status(
            tid,
            str((task or {}).get("status") or "todo"),
            event_type="parent_goal_cancelled",
            event_actor="task_scoped_execution_service",
            event_details={"goal_id": goal_id, "goal_status": goal_status, "phase": phase},
        )
        return TaskScopedRouteResponse(
            data={
                "status": "skipped",
                "reason": "parent_goal_cancelled",
                "goal_status": goal_status,
                "task_id": tid,
                "goal_id": goal_id,
                "phase": phase,
            },
            status="skipped",
            message="Parent goal is terminal",
            code=409,
        )


task_scoped_execution_service = TaskScopedExecutionService()


def get_task_scoped_execution_service() -> TaskScopedExecutionService:
    return task_scoped_execution_service
