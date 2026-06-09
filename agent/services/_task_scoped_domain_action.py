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
