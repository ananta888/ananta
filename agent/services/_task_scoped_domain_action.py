"""Domain-action router cluster for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
domain_action cluster of SPLIT-001 (sub-splits 001e-1). The module owns
one concern: building the domain-action router, resolving the
domain-action payload from a task/command, registering goal-artifact
outputs after a domain action, and executing a domain action end-to-end.

The methods here are tightly coupled to the DomainRegistry /
CapabilityRegistry / DomainPolicyLoader / DomainPolicyService /
DomainActionRouter / BridgeAdapterRegistry stack (all imported from
``agent.services``) and to the task-execution finalization service
(``get_core_services().task_execution_service.finalize_task_execution_response``).
They are not pure — they touch goal-artifact state, config snapshots,
prompt snapshots, and execution provenance. They are isolated from
the rest of the task-scoped service so that future domain-action
extensions land here rather than re-bloating the service.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window).

Cluster ownership: **domain_action** (router + payload + register +
execute). The remaining domain_action methods (propose_task_with_comparisons,
finalize_interactive_terminal_execution, execute_research_artifact)
are split out in 001e-2.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent.common.errors import TaskConflictError
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.runtime_policy import build_trace_record
from agent.services.bridge_adapter_registry import BridgeAdapterRegistry
from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_loader import DomainPolicyLoader
from agent.services.domain_policy_service import DomainPolicyService
from agent.services.domain_registry import DomainRegistry
from agent.services.service_registry import get_core_services


if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 with trailing 'Z'.

    Note: the original ``_now_iso`` reference inside
    ``task_scoped_execution_service._register_goal_artifact_outputs``
    (line 1694) was a NameError waiting to happen — the function was
    never defined in the service module. By moving the call site to
    this module we both gain SRP and fix the latent bug.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ======================================================================
# 1. Router construction
# ======================================================================


def build_domain_action_router() -> DomainActionRouter:
    domain_registry = DomainRegistry()
    descriptors = domain_registry.load()
    capability_registry = CapabilityRegistry()
    capability_registry.load_from_descriptors(descriptors)
    policy_loader = DomainPolicyLoader(capability_registry=capability_registry)
    policy_service = DomainPolicyService(capability_registry=capability_registry)
    bridge_adapter_registry = BridgeAdapterRegistry()
    bridge_adapter_registry.load_from_descriptors(descriptors)
    return DomainActionRouter(
        domain_registry=domain_registry,
        capability_registry=capability_registry,
        policy_loader=policy_loader,
        policy_service=policy_service,
        bridge_adapter_registry=bridge_adapter_registry,
    )


# Backward-compat alias for the pre-split private name.
_build_domain_action_router = build_domain_action_router


# ======================================================================
# 2. Payload resolution (pure — no side effects)
# ======================================================================


def resolve_domain_action_payload(*, task: dict, command: str | None) -> dict:
    command_text = str(command or "").strip()
    inline_payload = None
    if command_text:
        try:
            parsed = json.loads(command_text)
        except json.JSONDecodeError as exc:
            raise TaskConflictError(
                "domain_action_payload_invalid",
                details={"reason": "command_must_be_valid_json_object", "error": str(exc)},
            )
        if not isinstance(parsed, dict):
            raise TaskConflictError(
                "domain_action_payload_invalid",
                details={"reason": "command_must_be_json_object"},
            )
        inline_payload = dict(parsed)
    payload = inline_payload or dict(task.get("domain_action_request") or {})
    if not payload:
        raise TaskConflictError(
            "domain_action_payload_missing",
            details={"reason": "provide_json_command_or_domain_action_request"},
        )
    required = ("domain_id", "capability_id", "action_id")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise TaskConflictError(
            "domain_action_payload_invalid",
            details={"reason": "missing_required_fields", "fields": missing},
        )
    return payload


_resolve_domain_action_payload = resolve_domain_action_payload


# ======================================================================
# 3. Goal-artifact output registration (side-effecting: goal_artifacts,
#    config_snapshots, prompt_snapshots, execution_provenance)
# ======================================================================


def register_goal_artifact_outputs(
    *,
    task: dict,
    tid: str,
    artifact_refs: list[dict],
    get_system_prompt_for_task,
) -> list[dict]:
    """Register goal artifact outputs for a completed task.

    ``get_system_prompt_for_task`` is passed in by the caller (the
    service) to keep this module free of any back-reference to the
    service class. Tests that need to override the system prompt
    continue to monkeypatch
    ``TaskScopedExecutionService._get_system_prompt_for_task``;
    the delegating wrapper in the service forwards that patched value
    to this function.
    """
    goal_id = str((task or {}).get("goal_id") or "").strip()
    if not goal_id:
        return []
    if not list(artifact_refs or []):
        return []
    from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
    from agent.services.config_snapshot_service import ConfigSnapshotService
    from agent.services.prompt_snapshot_service import PromptSnapshotService

    execution_context = dict((task or {}).get("worker_execution_context") or {})
    context_envelope = execution_context.get("context_envelope_ref")
    context_envelope = dict(context_envelope or {}) if isinstance(context_envelope, dict) else {}
    source_usage_refs = [str(item) for item in list(context_envelope.get("source_usage_refs") or []) if str(item).strip()]
    context_artifact_refs = [
        str(item.get("artifact_ref") or item.get("ref") or "").strip()
        for item in list(context_envelope.get("retrieval_refs") or [])
        if isinstance(item, dict)
    ]
    service = GoalArtifactService()
    config_snapshot_service = ConfigSnapshotService()
    prompt_snapshot_service = PromptSnapshotService()
    if context_artifact_refs and not source_usage_refs:
        context_tracking = service.validate_and_record_context_usages(
            goal_id=goal_id,
            artifact_refs=[item for item in context_artifact_refs if item],
            task_id=tid,
            worker_id=str((task or {}).get("assigned_worker_id") or "").strip() or None,
            context_hash=str(context_envelope.get("context_hash") or "").strip() or None,
        )
        source_usage_refs = list(context_tracking.get("source_usage_refs") or [])
    worker_id = str((task or {}).get("assigned_worker_id") or "").strip() or None
    worker_profile = str(execution_context.get("worker_profile") or "default")
    runtime_path = str(((task or {}).get("verification_status") or {}).get("routing", {}).get("runtime_path") or "unknown")
    backend = str(((task or {}).get("verification_status") or {}).get("routing", {}).get("backend") or "unknown")
    model_name = str(((task or {}).get("verification_status") or {}).get("routing", {}).get("inference_model") or "unknown")
    execution_seed = f"{goal_id}:{tid}:{worker_id or 'worker'}"
    execution_id = f"exec-{hashlib.sha1(execution_seed.encode('utf-8')).hexdigest()[:14]}"
    worker_config = config_snapshot_service.build_snapshot(
        config_kind="worker_config",
        source_path_or_ref=f"task:{tid}:worker",
        scope=f"goal:{goal_id}",
        config_payload={"worker_profile": worker_profile, "worker_id": worker_id or "unknown"},
    )
    runtime_config = config_snapshot_service.build_snapshot(
        config_kind="runtime_config",
        source_path_or_ref=f"task:{tid}:runtime",
        scope=f"goal:{goal_id}",
        config_payload={"runtime_path": runtime_path, "backend": backend},
    )
    model_config = config_snapshot_service.build_snapshot(
        config_kind="model_config",
        source_path_or_ref=f"task:{tid}:model",
        scope=f"goal:{goal_id}",
        config_payload={"model": model_name},
    )
    policy_config = config_snapshot_service.build_snapshot(
        config_kind="policy_config",
        source_path_or_ref=f"task:{tid}:policy",
        scope=f"goal:{goal_id}",
        config_payload={"data_boundary": "project_private", "sensitivity": "internal"},
    )
    system_prompt = get_system_prompt_for_task(str(tid)) or ""
    prompt_refs: dict[str, Any] = {"no_prompt_reason": "no_prompt_used"} if not system_prompt else {}
    if system_prompt:
        template = prompt_snapshot_service.build_template_snapshot(
            prompt_template_ref=f"prompt-template:{tid}",
            template_path=f"task:{tid}:resolved-template",
            template_version="v1",
            template_text=system_prompt,
            renderer="replace",
            expected_output_schema_ref="worker_response.v1",
        )
        final_prompt = prompt_snapshot_service.build_final_prompt_record(
            prompt_template_ref=template["prompt_template_ref"],
            variables_payload={"task_id": tid, "goal_id": goal_id},
            final_prompt_text=system_prompt,
            context_hash=str(context_envelope.get("context_hash") or "context-hash-missing"),
            input_usage_refs=list(source_usage_refs or []),
            output_schema_ref="worker_response.v1",
            store_raw_prompt=False,
        )
        prompt_refs = {
            "prompt_template_ref": template.get("prompt_template_ref"),
            "prompt_template_version": template.get("template_version"),
            "prompt_template_hash": template.get("template_hash"),
            "prompt_variables_hash": final_prompt.get("variables_hash"),
            "final_prompt_hash": final_prompt.get("final_prompt_hash"),
            "redacted_prompt_ref": final_prompt.get("storage_ref"),
            "raw_prompt_stored": final_prompt.get("raw_prompt_stored"),
        }
    provenance = {
        "schema": "execution_provenance.v1",
        "provenance_id": f"prov-{hashlib.sha1(f'{goal_id}:{tid}:{execution_id}'.encode('utf-8')).hexdigest()[:16]}",
        "goal_id": goal_id,
        "task_id": str(tid),
        "execution_id": execution_id,
        "worker_id": str(worker_id or "worker-unknown"),
        "worker_kind": "native",
        "runtime_target_ref": {"runtime_type": backend, "location": "local", "snapshot_id": runtime_config.get("config_snapshot_id")},
        "model_ref": {"provider_id": backend, "model_id": model_name},
        "config_refs": {
            "worker_config_ref": worker_config.get("config_snapshot_id"),
            "runtime_config_ref": runtime_config.get("config_snapshot_id"),
            "model_config_ref": model_config.get("config_snapshot_id"),
            "policy_config_ref": policy_config.get("config_snapshot_id"),
        },
        "prompt_refs": prompt_refs,
        "input_usage_refs": list(source_usage_refs or []),
        "output_artifact_refs": [
            str(item.get("artifact_id") or item.get("trace_bundle_ref") or item.get("workspace_relative_path") or "")
            for item in list(artifact_refs or [])
            if isinstance(item, dict)
        ],
        "created_at": _now_iso(),
    }
    try:
        return service.register_output_artifacts_from_refs(
            goal_id=goal_id,
            task_id=str(tid),
            worker_id=worker_id,
            artifact_refs=list(artifact_refs or []),
            input_usage_refs=source_usage_refs,
            execution_provenance=provenance,
        )
    except GoalArtifactServiceError as exc:
        return [
            {
                "status": "failed",
                "reason_code": exc.reason_code,
                "detail": exc.detail,
            }
        ]


_register_goal_artifact_outputs = register_goal_artifact_outputs


# ======================================================================
# 4. End-to-end domain-action execution
# ======================================================================


def execute_domain_action(
    *,
    tid: str,
    task: dict,
    task_kind: str,
    request_data,
    command: str | None,
    reason: str,
    execution_policy,
) -> "TaskScopedRouteResponse":
    """Route + execute a domain action and return a TaskScopedRouteResponse.

    Side effects: writes a pipeline trace stage + trace record, then
    delegates finalization to the core task-execution service. The
    domain action is a router+policy decision; no shell is invoked.

    Note: ``TaskScopedRouteResponse`` is imported lazily at call time
    (not at module import) to keep this module free of any
    back-reference to the service class. Tests that monkeypatch
    ``TaskScopedRouteResponse`` continue to work because the runtime
    import resolves to the same class object.
    """
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    payload = resolve_domain_action_payload(task=task, command=command)
    route_result = build_domain_action_router().route(
        domain_id=str(payload.get("domain_id") or "").strip(),
        capability_id=str(payload.get("capability_id") or "").strip(),
        action_id=str(payload.get("action_id") or "").strip(),
        execution_mode=str(payload.get("execution_mode") or "execute").strip() or "execute",
        context_summary=dict(payload.get("context_summary") or {}),
        actor_metadata=dict(payload.get("actor_metadata") or {}),
        approval=dict(payload.get("approval") or {}) if isinstance(payload.get("approval"), dict) else None,
    )
    route = route_result.as_dict()

    state = str(route.get("state") or "").strip().lower()
    if state in {"plan", "execution_started"}:
        status = "completed"
        exit_code = 0
        failure_type = "success"
    elif state == "approval_required":
        status = "blocked"
        exit_code = 1
        failure_type = "approval_required"
    elif state == "denied":
        status = "failed"
        exit_code = 1
        failure_type = "policy_denied"
    else:
        status = "failed"
        exit_code = 1
        failure_type = "degraded"

    pipeline = new_pipeline_trace(
        pipeline="task_execute",
        task_kind=task_kind,
        policy_version="domain_action_router_v1",
        metadata={
            "task_id": tid,
            "domain_id": route.get("domain_id"),
            "capability_id": route.get("capability_id"),
            "action_id": route.get("action_id"),
        },
    )
    append_stage(
        pipeline,
        name="domain_action_route",
        status="ok" if status == "completed" else "failed",
        metadata={"route_state": state, "route_reason": route.get("reason")},
    )
    trace = build_trace_record(
        task_id=tid,
        event_type="execution_result",
        task_kind=task_kind,
        backend="domain_action_router",
        requested_backend="domain_action_router",
        routing_reason="domain_action_router",
        policy_version="domain_action_router_v1",
        metadata={
            "source": "domain_action_execute",
            "domain_action_route": route,
        },
    )
    response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
        tid=tid,
        task=task,
        status=status,
        reason=reason or "Domain action routed",
        command=command,
        tool_calls=request_data.tool_calls if isinstance(getattr(request_data, "tool_calls", None), list) else None,
        output=json.dumps(route, ensure_ascii=False),
        exit_code=exit_code,
        retries_used=0,
        retry_history=[],
        failure_type=failure_type,
        execution_duration_ms=0,
        trace=trace,
        pipeline={**pipeline, "trace_id": trace["trace_id"]},
        execution_policy=execution_policy,
        extra_history={
            "domain_action_route": route,
            "domain_action_payload": payload,
        },
    )
    return TaskScopedRouteResponse(data=response_payload)


_execute_domain_action = execute_domain_action


# --- SPLIT-001e-2: propose_task_with_comparisons, finalize_interactive_terminal, execute_research_artifact ---

import concurrent.futures
import time
from typing import Callable

from flask import current_app

from agent.common.sgpt import SUPPORTED_CLI_BACKENDS
from agent.common.utils.structured_action_utils import extract_structured_action_fields
from agent.models import TaskStepExecuteRequest
from agent.research_backend import is_research_backend
from agent.services.worker_routing_policy_utils import derive_required_capabilities, derive_research_specialization
from agent.runtime_policy import normalize_task_kind, runtime_routing_config
from agent.services.execution_improvement_loop_service import get_execution_improvement_loop_service
from agent.services.task_execution_policy_service import resolve_execution_policy
from agent.services.verification_service import get_verification_service
from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.utils import _extract_reason

_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def propose_task_with_comparisons(
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
    resolve_requested_model: Callable,
    invoke_cli_runner: Callable,
    coalesce_cli_output: Callable,
    resolve_task_propose_timeout: Callable,
) -> "TaskScopedRouteResponse":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from agent.common.sgpt import SUPPORTED_CLI_BACKENDS as _BACKENDS
    from agent.runtime_policy import resolve_cli_backend as _resolve_cli_backend_fn
    from agent.services._task_scoped_citation import build_flow_metrics_payload
    from agent.services._task_scoped_config_policy import normalize_temperature
    from agent.services._task_scoped_repair import build_llm_call_profile_entries
    from agent.services._task_scoped_runtime import build_research_result, build_review_state, routing_dimensions as _routing_dimensions

    def resolve_cli_backend(task_kind, *, requested_backend, agent_cfg, required_capabilities):
        from flask import current_app as _ca
        from agent.common.sgpt import SUPPORTED_CLI_BACKENDS as _sbs
        backend, reason, _ = _resolve_cli_backend_fn(
            task_kind=task_kind,
            requested_backend=requested_backend,
            supported_backends=_sbs,
            agent_cfg=agent_cfg if agent_cfg is not None else (_ca.config.get("AGENT_CONFIG", {}) or {}),
            fallback_backend="sgpt",
            required_capabilities=required_capabilities,
        )
        return backend, reason

    task_kind = normalize_task_kind(None, base_prompt)
    workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
    requested_temperature = normalize_temperature(getattr(request_data, "temperature", None))
    timeout = resolve_task_propose_timeout(cfg, task_kind)
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
        selected_model = resolve_requested_model(
            agent_cfg=cfg,
            requested_model=(parts[1].strip() if len(parts) > 1 else "") or request_data.model,
        )
        if requested_backend not in SUPPORTED_CLI_BACKENDS:
            return entry, {"error": f"unsupported_backend:{requested_backend}", "backend": requested_backend}

        effective_backend, routing_reason = resolve_cli_backend(
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
        rc, cli_out, cli_err, backend_used = invoke_cli_runner(cli_runner, **cli_kwargs)
        latency_ms = int((time.time() - started_at) * 1000)
        raw_res, output_source = coalesce_cli_output(cli_out, cli_err)
        required_capabilities = derive_required_capabilities(task, task_kind)
        dims = _routing_dimensions(
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
            **dims,
        }
        cli_result = {
            "returncode": rc,
            "latency_ms": latency_ms,
            "stderr_preview": (cli_err or "")[:240],
            "output_source": output_source,
            "llm_call_profile": build_llm_call_profile_entries(
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
            research_res = build_research_result(
                raw_res=raw_res,
                backend_used=backend_used,
                tid=tid,
                rc=rc,
                cli_err=cli_err,
                latency_ms=latency_ms,
                output_source=output_source,
                research_context=research_context,
            )
            research_res["model"] = selected_model
            research_res["routing"] = routing
            return entry, research_res
        command, tool_calls = extract_structured_action_fields(raw_res)
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
    review = build_review_state(
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
            "flow_metrics": build_flow_metrics_payload(
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


def finalize_interactive_terminal_execution(
    *,
    tid: str,
    task: dict,
    reason: str,
    execution_policy,
) -> "TaskScopedRouteResponse":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from agent.services._task_scoped_citation import build_flow_metrics_payload

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
            "flow_metrics": build_flow_metrics_payload(
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


def execute_research_artifact(
    *,
    tid: str,
    task: dict,
    proposal: dict,
    research_artifact: dict,
    execution_policy,
) -> "TaskScopedRouteResponse":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from agent.services._task_scoped_citation import build_flow_metrics_payload
    from agent.services._task_scoped_runtime import verify_research_artifact

    review = (proposal.get("review") or {}) if isinstance(proposal, dict) else {}
    if review.get("required") and review.get("status") != "approved":
        raise TaskConflictError("research_review_required", details={"review": review, "task_id": tid})
    verification = verify_research_artifact(research_artifact)
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
            "flow_metrics": build_flow_metrics_payload(
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
