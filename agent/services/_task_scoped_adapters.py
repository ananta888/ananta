"""Adapter cluster (Hermes + task-handler) for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
adapters cluster of SPLIT-001 (sub-split 001g). The module owns the two
non-CLI execution paths: the HermesAdapter worker path (HF-T019/T020/T021)
and the registered TaskHandler path (FA-T003).

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from flask import current_app

from agent.services.service_registry import get_core_services
from agent.services.task_handler_registry import get_task_handler_registry

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def try_hermes_propose(
    *,
    tid: str,
    task: dict,
    task_kind: str,
    request_data,
    research_context: object,
    cfg: dict,
) -> "TaskScopedRouteResponse | None":
    """Invoke HermesAdapter when ToolRouter selects Hermes for safe proposal modes. HF-T019."""
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    hermes_cfg_raw = dict((cfg or {}).get("hermes_worker_adapter") or {})
    if not bool(hermes_cfg_raw.get("enabled", False)):
        return None
    feature_flags = dict((cfg or {}).get("feature_flags") or {})
    if not bool(feature_flags.get("enable_hermes_worker_adapter", False)):
        return None

    # Only safe proposal modes — never mutation
    safe_modes = {"plan_only", "review", "summarize", "patch_propose", "research_limited"}
    if task_kind not in safe_modes:
        return None

    try:
        result = invoke_hermes_adapter(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            research_context=research_context,
            hermes_cfg_raw=hermes_cfg_raw,
        )
    except Exception as exc:
        # HF-T022: Hermes unavailable → return degraded, not crash
        return TaskScopedRouteResponse(
            data={
                "status": "degraded",
                "reason": "hermes_unavailable",
                "fallback_from_hermes": True,
                "error": str(exc)[:200],
                "task_id": tid,
            },
            status="degraded",
            message="Hermes unavailable; no policy-approved fallback",
            code=503,
        )

    if result is None:
        return None

    status = str(result.get("status") or "").lower()
    if status in {"denied", "degraded", "failed"}:
        # HF-T022: record Hermes failure explicitly; don't hide it
        return TaskScopedRouteResponse(
            data={**result, "fallback_from_hermes": False, "task_id": tid},
            status=status,
            message=f"Hermes returned {status}",
            code=400 if status == "denied" else 503,
        )
    return TaskScopedRouteResponse(data={**result, "backend": "hermes", "task_id": tid})


def invoke_hermes_adapter(
    *,
    tid: str,
    task: dict,
    task_kind: str,
    request_data,
    research_context: object,
    hermes_cfg_raw: dict,
) -> dict | None:
    """Build envelope, context blocks, run HermesAdapter. HF-T019, T020, T021."""
    from worker.core.hermes_adapter import HermesAdapter
    from worker.core.hermes_adapter_config import HermesAdapterConfig
    from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
    from agent.services._task_scoped_hermes_context import build_hermes_context_blocks

    try:
        hermes_config = HermesAdapterConfig(**{
            k: v for k, v in hermes_cfg_raw.items()
            if k in HermesAdapterConfig.model_fields
        })
    except Exception:
        hermes_config = HermesAdapterConfig()

    adapter = HermesAdapter(config=hermes_config)

    # Build minimal envelope for Hermes — planning cap covers review/plan_only
    cap_map = {
        "plan_only": ["planning"],
        "review": ["planning", "review"],
        "summarize": ["planning", "summarize"],
        "patch_propose": ["planning", "patch_propose"],
        "research_limited": ["planning", "research_limited"],
    }
    capabilities = cap_map.get(task_kind, ["planning"])
    envelope = ExecutionEnvelope(
        task_id=tid,
        actor_ref="task_scoped_execution_service:hermes",
        capability_grant=CapabilityGrant(capabilities=capabilities),
        context_envelope_ref=f"task:{tid}",
        audit_correlation_id=f"hermes:{tid}:{task_kind}",
    )

    # HF-T020: build context blocks from task/research context
    context_blocks = build_hermes_context_blocks(
        task=task,
        request_data=request_data,
        research_context=research_context,
    )

    # Run adapter for the requested mode
    mode_method = getattr(adapter, task_kind, adapter.plan_only)
    worker_result = mode_method(envelope, context_blocks=context_blocks)

    # HF-T021: convert WorkerResult artifacts to task response format
    artifacts = []
    for art in (worker_result.artifacts or []):
        artifacts.append({
            "artifact_id": art.artifact_id,
            "kind": art.kind,
            "provenance": art.provenance,
            "summary": art.summary,
            "metadata": dict(art.metadata or {}),
            "source": "hermes",
        })

    return {
        "status": worker_result.status.value,
        "summary": worker_result.summary,
        "artifacts": artifacts,
        "artifact_refs": [a["artifact_id"] for a in artifacts],
        "policy_observations": list(worker_result.policy_observations or []),
        "warnings": list(worker_result.warnings or []),
        "no_side_effects_confirmed": worker_result.no_side_effects_confirmed,
        "backend": "hermes",
        "adapter_mode": task_kind,
    }


def try_handler_propose(
    *,
    tid: str,
    task: dict,
    task_kind: str,
    request_data,
    base_prompt: str,
    cli_runner: Callable,
    forwarder: Callable,
    tool_definitions_resolver: Callable,
    service: object,
    build_review_state: Callable,
) -> "TaskScopedRouteResponse | None":
    """FA-T003: Maps to 'deterministic_handler' strategy."""
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    registry = get_task_handler_registry()
    handler = registry.resolve(task_kind)
    if handler is None or not hasattr(handler, "propose"):
        return None
    handler_descriptor = registry.resolve_descriptor(task_kind) or {}
    response = handler.propose(
        tid=tid,
        task=task,
        task_kind=task_kind,
        request_data=request_data,
        base_prompt=base_prompt,
        service=service,
        cli_runner=cli_runner,
        forwarder=forwarder,
        tool_definitions_resolver=tool_definitions_resolver,
        handler_descriptor=handler_descriptor,
    )
    coerced = coerce_handler_response(response)
    if coerced is None:
        return None
    payload = dict(coerced.data or {})
    payload.setdefault("handler_contract", handler_descriptor or None)
    if bool((handler_descriptor.get("safety_flags") or {}).get("requires_review")) and "review" not in payload:
        base_review = build_review_state(
            current_app.config.get("AGENT_CONFIG", {}) or {},
            backend="handler",
            task_kind=task_kind,
            command=str(payload.get("command") or "") or None,
            tool_calls=payload.get("tool_calls"),
        )
        payload["review"] = {
            **base_review,
            "required": True,
            "status": "pending",
            "reason": "handler_safety_requires_review",
        }
    try:
        get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=str(payload.get("reason") or payload.get("status") or "handler_proposal"),
            raw=json.dumps(payload, ensure_ascii=False),
            backend="handler",
            model=None,
            routing={
                "task_kind": task_kind,
                "effective_backend": "handler",
                "reason": "registered_task_handler",
                "required_capabilities": list(handler_descriptor.get("capabilities") or []),
            },
            cli_result={
                "returncode": 0,
                "latency_ms": 0,
                "output_source": "handler",
            },
            worker_context={"handler_task_kind": task_kind},
            trace={"trace_id": f"handler-{tid}", "policy_version": "v1"},
            review=payload.get("review") if isinstance(payload.get("review"), dict) else None,
            command=payload.get("command") if isinstance(payload.get("command"), str) else None,
            tool_calls=payload.get("tool_calls") if isinstance(payload.get("tool_calls"), list) else None,
            history_event={
                "event_type": "proposal_result",
                "reason": str(payload.get("reason") or payload.get("status") or "handler_proposal"),
                "backend": "handler",
                "routing_reason": "registered_task_handler",
            },
        )
    except Exception:
        pass
    return TaskScopedRouteResponse(
        data=payload,
        status=coerced.status,
        message=coerced.message,
        code=coerced.code,
    )


def try_handler_execute(
    *,
    tid: str,
    task: dict,
    task_kind: str,
    request_data,
    forwarder: Callable,
    service: object,
) -> "TaskScopedRouteResponse | None":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    registry = get_task_handler_registry()
    handler = registry.resolve(task_kind)
    if handler is None or not hasattr(handler, "execute"):
        return None
    handler_descriptor = registry.resolve_descriptor(task_kind) or {}
    response = handler.execute(
        tid=tid,
        task=task,
        task_kind=task_kind,
        request_data=request_data,
        service=service,
        forwarder=forwarder,
        handler_descriptor=handler_descriptor,
    )
    coerced = coerce_handler_response(response)
    if coerced is None:
        return None
    payload = dict(coerced.data or {})
    payload.setdefault("handler_contract", handler_descriptor or None)
    return TaskScopedRouteResponse(
        data=payload,
        status=coerced.status,
        message=coerced.message,
        code=coerced.code,
    )


def coerce_handler_response(response: object | None) -> "TaskScopedRouteResponse | None":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from worker.core.propose import ExecutableProposal

    if response is None:
        return None
    if isinstance(response, TaskScopedRouteResponse):
        return response
    if isinstance(response, ExecutableProposal):
        return TaskScopedRouteResponse(
            data={
                **response.to_dict(),
                "status": "executable",
                "proposal_status": "executable",
                "selected_strategy": "deterministic_handler",
            }
        )
    if isinstance(response, dict):
        return TaskScopedRouteResponse(data=response)
    raise TypeError("task_handler_response_must_be_dict_or_TaskScopedRouteResponse")
