"""Propose orchestrator path for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as sub-split 001n.
Owns the ProposeStrategyOrchestrator-based proposal flow: policy resolution,
context compaction, orchestrator run, result persistence, and response assembly.

Backwards compatibility preserved via delegating wrapper in
:class:`TaskScopedExecutionService` (12-month deprecation window).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from flask import current_app, g, has_app_context, has_request_context

from agent.llm_integration import normalize_llm_call_profile_entry
from agent.routes.tasks.orchestration_policy import derive_required_capabilities
from agent.runtime_policy import normalize_task_kind
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.planning_context_compactor_service import get_planning_context_compactor_service
from agent.services.propose_policy_service import get_propose_policy_service
from agent.services.service_registry import get_core_services
from agent.services.propose_policy import get_task_kind_preset
from agent.services.product_event_service import record_product_event
from agent.routes.tasks.utils import update_local_task_status

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def run_propose_orchestrator_path(
    *,
    tid: str,
    task: dict,
    request_data,
    base_prompt: str,
    research_context_summary: dict | None,
    task_kind: str,
    citation_contract: str | None,
    cfg: dict,
    source_catalog: dict | None,
    scoped_resolution_source: str,
    cli_runner: Callable,
    tool_definitions_resolver: Callable,
    resolve_cli_backend: Callable,
    resolve_task_propose_timeout: Callable,
    invoke_cli_runner: Callable,
    coalesce_cli_output: Callable,
    get_system_prompt_for_task: Callable,
    allow_synthetic_llm_profile_fallback: Callable,
) -> "TaskScopedRouteResponse":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from agent.services._task_scoped_runtime import routing_dimensions
    from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext
    from worker.core.propose import validate_executable_proposal
    from agent.services.propose_strategy_registry import build_strategy_registry

    propose_policy_override = task.get("propose_policy_override", {})
    task_override = {}
    if getattr(request_data, "strategy_mode", None):
        task_override["strategy_mode"] = str(getattr(request_data, "strategy_mode")).strip().lower()
    policy = get_propose_policy_service().get_effective_policy(
        task_kind=task_kind,
        task_override=task_override or None,
        project_config=cfg,
        admin_overrides=propose_policy_override,
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
    system_prompt = get_system_prompt_for_task(tid)
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
        fallback_backend, fallback_reason = resolve_cli_backend(
            task_kind,
            requested_backend="auto",
            agent_cfg=cfg,
            required_capabilities=derive_required_capabilities(task, task_kind),
        )
        cli_backend = fallback_backend if fallback_backend != "ananta-worker" else "aider"
        try:
            rc, cli_out, cli_err, backend_used = invoke_cli_runner(
                cli_runner,
                prompt=str(base_prompt or ""),
                options=["--no-interaction"],
                timeout=resolve_task_propose_timeout(cfg, task_kind),
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
            raw_res, _output_source = coalesce_cli_output(cli_out, cli_err)
            parsed = json.loads(str(raw_res or "{}"))
            fallback_command = str(parsed.get("command") or "").strip() or None
            fallback_tool_calls = parsed.get("tool_calls") if isinstance(parsed.get("tool_calls"), list) else []
            fallback_reason = str(parsed.get("reason") or result.reason or "fallback_cli_proposal").strip()
            _usable = rc == 0 or (rc != 0 and _output_source == "stderr" and bool(fallback_command or fallback_tool_calls))
            if _usable and (fallback_command or fallback_tool_calls):
                result_dict["command"] = fallback_command
                result_dict["tool_calls"] = fallback_tool_calls
                result_dict["reason"] = fallback_reason
                result_dict["backend"] = backend_used
                result_dict["status"] = "executable"
        except Exception:
            pass

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
        "goal_config_source": scoped_resolution_source,
        "runtime_selection": {
            "provider": cfg.get("default_provider"),
            "model": cfg.get("default_model"),
            "backend": _runtime_backend,
            "source": scoped_resolution_source,
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
    if not llm_call_profile and allow_synthetic_llm_profile_fallback():
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
            "goal_config_source": scoped_resolution_source,
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

    routing_dims = routing_dimensions(
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
            "goal_config_source": scoped_resolution_source,
            **routing_dims,
        },
    })
