"""Opt-in task-bound entry point; policy blocks never fall through to an LLM."""

from __future__ import annotations

import threading

from agent.services.hub_multi_tool_execution import ToolPlanScope


class HubMultiToolPipeline:
    def __init__(self, *, router, executor, task_lookup=None):
        self._router, self._executor = router, executor
        self._task_lookup = task_lookup

    def try_execute(self, *, prompt: str, task: dict | None, agent_cfg: dict, cancel_check=lambda: False):
        direct = agent_cfg.get("hub_direct_execution") or {}
        cfg = direct.get("multi_tool_plan") or {}
        if not direct.get("enabled") or not cfg.get("multi_tool_plan") or cfg.get("mode", "disabled") == "disabled":
            return None
        if not task or task.get("organization_id") or task.get("derivation_reason") == "goal_task_recovery":
            return None
        from agent.services.hub_tool_execution_adapter import derive_mutation_mode
        from agent.services.task_execution_policy_service import resolve_task_scope_allowed_tools

        allowed = tuple(sorted(set(resolve_task_scope_allowed_tools(task)) & set(direct.get("allowed_tools") or [])))
        scope = ToolPlanScope(
            str(task.get("tenant_id") or ""),
            str(task.get("project_id") or ""),
            str(task.get("id") or ""),
            str(task.get("workspace_dir") or ""),
            allowed,
            derive_mutation_mode(task, agent_cfg),
            task.get("goal_id"),
        )
        try:
            scope.validate()
        except ValueError:
            return None
        if task.get("status") in {"done", "completed", "cancelled", "failed", "archived"}:
            return {"status": "blocked", "reason": "plan_task_terminal"}
        decision = self._router.plan(
            prompt=prompt,
            allowed_tools=allowed,
            config=cfg,
            mutation_mode=scope.mutation_mode,
            cancel_check=cancel_check,
        )
        if decision.status != "plan" or decision.shadow or decision.plan is None:
            return None

        def fenced():
            if cancel_check():
                return True
            if self._task_lookup is None:
                return False
            current = self._task_lookup(scope.task_id)
            if not current or current.get("status") in {"done", "completed", "cancelled", "failed", "archived"}:
                return True
            return any(
                current.get(key) != task.get(key) for key in ("tenant_id", "project_id", "workspace_dir", "goal_id")
            ) or not set(allowed) <= set(resolve_task_scope_allowed_tools(current))

        return self._executor.execute(
            decision.plan,
            scope=scope,
            agent_cfg=agent_cfg,
            timeout_seconds=cfg.get("execution_timeout_seconds", 30),
            max_calls=cfg.get("max_plan_calls", 4),
            cancel_check=fenced,
        )


def get_hub_multi_tool_pipeline():
    from flask import current_app, has_app_context

    if not has_app_context() or current_app.config.get("ROLE") != "hub":
        raise ValueError("multi_tool_hub_context_required")

    from agent.repositories.tool_plan_receipts import ToolPlanAuditStore, ToolPlanReceiptStore
    from agent.services.ananta_tool_policy_service import get_ananta_tool_policy_service
    from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
    from agent.services.hub_multi_tool_execution import HubMultiToolExecutionService
    from agent.services.hub_tool_execution_adapter import get_hub_tool_execution_adapter
    from agent.services.task_runtime_service import get_local_task_status
    from agent.services.tiny_router.service import get_tiny_tool_router_service

    with _pipeline_lock:
        pipeline = current_app.extensions.get("hub_multi_tool_pipeline")
        if pipeline is None:
            pipeline = HubMultiToolPipeline(
                router=get_tiny_tool_router_service(),
                task_lookup=get_local_task_status,
                executor=HubMultiToolExecutionService(
                    registry=get_ananta_tool_registry_service(),
                    policy=get_ananta_tool_policy_service(),
                    gateway=get_hub_tool_execution_adapter(),
                    receipts=ToolPlanReceiptStore(),
                    audit=ToolPlanAuditStore(),
                ),
            )
            current_app.extensions["hub_multi_tool_pipeline"] = pipeline
        return pipeline


_pipeline_lock = threading.Lock()
