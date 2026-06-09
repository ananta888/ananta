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

    # --- cluster: config_policy (resolvers, bounded_*, normalize) ---
    @staticmethod
    def _allow_synthetic_llm_profile_fallback() -> bool:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.allow_synthetic_llm_profile_fallback.
        from agent.services._task_scoped_config_policy import (
            allow_synthetic_llm_profile_fallback,
        )
        return allow_synthetic_llm_profile_fallback()

    @staticmethod
    def _is_interactive_terminal_session(session_payload: dict | None) -> bool:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.is_interactive_terminal_session.
        from agent.services._task_scoped_config_policy import (
            is_interactive_terminal_session,
        )
        return is_interactive_terminal_session(session_payload)

    @staticmethod
    def _normalize_temperature(value: float | int | str | None) -> float | None:
        # Delegating wrapper for SPLIT-001b. Implementation lives in
        # agent.services._task_scoped_config_policy.normalize_temperature.
        from agent.services._task_scoped_config_policy import normalize_temperature
        return normalize_temperature(value)

    @staticmethod
    def _default_model(agent_cfg: dict) -> str | None:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.default_model.
        from agent.services._task_scoped_config_policy import default_model
        return default_model(agent_cfg)

    @classmethod
    def _resolve_requested_model(cls, *, agent_cfg: dict, requested_model: str | None) -> str | None:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_requested_model.
        from agent.services._task_scoped_config_policy import (
            resolve_requested_model,
        )
        return resolve_requested_model(agent_cfg=agent_cfg, requested_model=requested_model)

    @staticmethod
    def _resolve_task_propose_timeout(agent_cfg: dict, task_kind: str) -> int:
        # SPLIT-001p: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_config_policy.resolve_task_propose_timeout.
        from agent.services._task_scoped_config_policy import (
            resolve_task_propose_timeout,
        )
        return resolve_task_propose_timeout(agent_cfg, task_kind)

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

    # --- cluster: workspace_runtime (command rewrite) ---
    @staticmethod
    def _rewrite_runtime_command_for_workspace_tools(*, command: str | None, workspace_dir: str | None) -> tuple[str | None, dict | None]:
        # SPLIT-001v: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_workspace_runtime.rewrite_runtime_command_for_workspace_tools.
        from agent.services._task_scoped_workspace_runtime import (
            rewrite_runtime_command_for_workspace_tools,
        )
        return rewrite_runtime_command_for_workspace_tools(
            command=command,
            workspace_dir=workspace_dir,
        )

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
        # SPLIT-001q: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_repair.is_interactive_timeout_like_failure.
        from agent.services._task_scoped_repair import (
            is_interactive_timeout_like_failure,
        )
        return is_interactive_timeout_like_failure(rc=rc, output=output, stderr=stderr)

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
        # SPLIT-001r: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_cli_invocation.invoke_cli_runner.
        from agent.services._task_scoped_cli_invocation import invoke_cli_runner
        return invoke_cli_runner(cli_runner, **cli_kwargs)

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
        from agent.services._task_scoped_propose_orch import run_propose_orchestrator_path
        return run_propose_orchestrator_path(
            tid=tid,
            task=task,
            request_data=request_data,
            base_prompt=base_prompt,
            research_context_summary=research_context_summary,
            task_kind=task_kind,
            citation_contract=citation_contract,
            cfg=cfg,
            source_catalog=source_catalog,
            scoped_resolution_source=scoped_resolution.source,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            resolve_cli_backend=self._resolve_cli_backend,
            resolve_task_propose_timeout=self._resolve_task_propose_timeout,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
            get_system_prompt_for_task=self._get_system_prompt_for_task,
            allow_synthetic_llm_profile_fallback=self._allow_synthetic_llm_profile_fallback,
        )


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

        from agent.services._task_scoped_execute_workspace import run_execute_workspace_path
        return run_execute_workspace_path(
            tid=tid,
            task=task,
            command=command,
            tool_calls=tool_calls,
            reason=reason,
            used_last_proposal=used_last_proposal,
            task_kind=task_kind,
            proposal_meta=proposal_meta,
            worker_profile=worker_profile,
            profile_source=profile_source,
            policy_classification_summary=policy_classification_summary,
            agent_cfg=agent_cfg,
            execution_policy=execution_policy,
            cli_runner=cli_runner,
            tool_definitions_resolver=tool_definitions_resolver,
            rewrite_runtime_command_for_workspace_tools=self._rewrite_runtime_command_for_workspace_tools,
            attempt_repaired_execute_after_meta_block=self._attempt_repaired_execute_after_meta_block,
            register_goal_artifact_outputs=self._register_goal_artifact_outputs,
        )


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
        from agent.services._task_scoped_domain_action import propose_task_with_comparisons
        return propose_task_with_comparisons(
            tid=tid,
            task=task,
            request_data=request_data,
            prompt=prompt,
            base_prompt=base_prompt,
            worker_context_meta=worker_context_meta,
            research_context=research_context,
            cli_runner=cli_runner,
            cfg=cfg,
            resolve_requested_model=self._resolve_requested_model,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
            resolve_task_propose_timeout=self._resolve_task_propose_timeout,
        )

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
    ) -> "TaskScopedRouteResponse":
        from agent.services._task_scoped_propose_single import propose_single_task_step
        return propose_single_task_step(
            tid=tid,
            task=task,
            request_data=request_data,
            base_prompt=base_prompt,
            research_context=research_context,
            cli_runner=cli_runner,
            cfg=cfg,
            tool_definitions_resolver=tool_definitions_resolver,
            allow_legacy_path=allow_legacy_path,
            resolve_requested_model=self._resolve_requested_model,
            invoke_cli_runner=self._invoke_cli_runner,
            coalesce_cli_output=self._coalesce_cli_output,
        )

    def _finalize_interactive_terminal_execution(
        self,
        *,
        tid: str,
        task: dict,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        from agent.services._task_scoped_domain_action import finalize_interactive_terminal_execution
        return finalize_interactive_terminal_execution(tid=tid, task=task, reason=reason, execution_policy=execution_policy)

    def _execute_research_artifact(
        self,
        *,
        tid: str,
        task: dict,
        proposal: dict,
        research_artifact: dict,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        from agent.services._task_scoped_domain_action import execute_research_artifact
        return execute_research_artifact(tid=tid, task=task, proposal=proposal, research_artifact=research_artifact, execution_policy=execution_policy)

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
        # SPLIT-001s: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_task_lookup.require_task.
        from agent.services._task_scoped_task_lookup import require_task
        return require_task(tid)

    def _maybe_sync_task_from_hub(self, tid: str) -> dict | None:
        # SPLIT-001s: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_task_lookup.maybe_sync_task_from_hub.
        from agent.services._task_scoped_task_lookup import maybe_sync_task_from_hub
        return maybe_sync_task_from_hub(tid)

    # --- cluster: runtime (cli backend, opencode session, interactive terminal, research) ---
    def _resolve_cli_backend(
        self,
        task_kind: str,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        required_capabilities: list[str] | None = None,
    ) -> tuple[str, str]:
        # SPLIT-001t: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_runtime.resolve_task_cli_backend.
        from agent.services._task_scoped_runtime import resolve_task_cli_backend
        return resolve_task_cli_backend(
            task_kind=task_kind,
            requested_backend=requested_backend,
            agent_cfg=agent_cfg,
            required_capabilities=required_capabilities,
        )

    @staticmethod
    def _coalesce_cli_output(stdout: str | None, stderr: str | None) -> tuple[str, str]:
        # SPLIT-001u: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_cli_invocation.coalesce_cli_output.
        from agent.services._task_scoped_cli_invocation import coalesce_cli_output
        return coalesce_cli_output(stdout, stderr)

    @classmethod
    def _sanitize_structured_output_text(cls, raw_text: str) -> str:
        return sanitize_structured_output_text(raw_text)

    @staticmethod
    def _normalize_tool_calls(tool_calls: object) -> list[dict] | None:
        # SPLIT-001u: delegating wrapper. Implementation lives in
        # agent.services._task_scoped_cli_invocation.normalize_tool_calls.
        from agent.services._task_scoped_cli_invocation import normalize_tool_calls
        return normalize_tool_calls(tool_calls)

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
        from agent.services._task_scoped_runtime import build_research_result
        return build_research_result(
            raw_res=raw_res,
            backend_used=backend_used,
            tid=tid,
            rc=rc,
            cli_err=cli_err,
            latency_ms=latency_ms,
            output_source=output_source,
            research_context=research_context,
        )

    def _verify_research_artifact(self, research_artifact: dict | None) -> dict:
        from agent.services._task_scoped_runtime import verify_research_artifact
        return verify_research_artifact(research_artifact)

    def _build_review_state(
        self,
        agent_cfg: dict,
        backend: str,
        task_kind: str,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
    ) -> dict:
        from agent.services._task_scoped_runtime import build_review_state
        return build_review_state(agent_cfg, backend, task_kind, command=command, tool_calls=tool_calls)

    def _get_worker_execution_context(
        self,
        task: dict | None,
        *,
        tid: str | None = None,
        base_prompt: str | None = None,
    ) -> dict:
        from agent.services._task_scoped_runtime import get_worker_execution_context
        return get_worker_execution_context(task, tid=tid, base_prompt=base_prompt)

    def _tool_definitions_for_task(
        self,
        task: dict | None,
        *,
        tool_definitions_resolver: Callable,
        execution_context: dict | None = None,
    ) -> list[dict]:
        from agent.services._task_scoped_runtime import tool_definitions_for_task
        return tool_definitions_for_task(task, tool_definitions_resolver=tool_definitions_resolver, execution_context=execution_context)

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
        from agent.services._task_scoped_runtime import resolve_task_role_identity
        return resolve_task_role_identity(tid, task)

    def _resolve_task_session_scope(self, *, tid: str, task: dict, policy: dict) -> tuple[str, str, str | None]:
        from agent.services._task_scoped_runtime import resolve_task_session_scope
        return resolve_task_session_scope(tid=tid, task=task, policy=policy)

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
        from agent.services._task_scoped_runtime import prepare_task_cli_session
        return prepare_task_cli_session(tid=tid, task=task, backend=backend, model=model, agent_cfg=agent_cfg)

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
        from agent.services._task_scoped_runtime import build_task_propose_prompt
        return build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=base_prompt,
            tool_definitions_resolver=tool_definitions_resolver,
            research_context=research_context,
            interactive_terminal=interactive_terminal,
            context_profile=context_profile,
        )

    def _get_system_prompt_for_task(self, tid: str) -> str | None:
        from agent.services._task_scoped_runtime import get_system_prompt_for_task
        return get_system_prompt_for_task(tid)

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
        from agent.services._task_scoped_runtime import routing_dimensions
        return routing_dimensions(
            backend_used=backend_used,
            model=model,
            temperature=temperature,
            requested_backend=requested_backend,
            agent_cfg=agent_cfg,
            worker_profile=worker_profile,
            profile_source=profile_source,
        )

    @staticmethod
    def _terminal_parent_goal_guard(*, tid: str, task: dict, phase: str) -> TaskScopedRouteResponse | None:
        from agent.services._task_scoped_runtime import terminal_parent_goal_guard
        return terminal_parent_goal_guard(tid=tid, task=task, phase=phase)


task_scoped_execution_service = TaskScopedExecutionService()


def get_task_scoped_execution_service() -> TaskScopedExecutionService:
    return task_scoped_execution_service
