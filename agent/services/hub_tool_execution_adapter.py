"""HDE-006..009: control plane for hub-direct tool execution.

This adapter is the bridge between the ananta-worker tool registry
(``agent/services/ananta_tool_registry_service.py``) and hub-direct
execution. It deliberately does **not** duplicate tool definitions and
does **not** use the classic ``agent.tools.registry`` — repo.*, git.*,
codecompass.* and test.* stay defined once in the ananta registry.

Responsibility split (HDW-DD-001/HDW-DD-004):
- Hub control plane (this module): policy gate, approval lifecycle,
  audit, result correlation.
- Execution plane (``worker_runtime_execution_adapter``): actually runs
  the tool inside an explicit workspace scope. This module never calls
  shell/script/workspace-mutating logic itself.

Result kinds (contract ``docs/contracts/hub-direct-execution.md``):
``direct_tool_result``, ``direct_policy_blocked``,
``direct_approval_required`` — plus ``direct_not_eligible`` /
``hub_direct_fallback_to_worker``, which the router/integration layer
emits before this adapter is reached.
"""
from __future__ import annotations

import uuid
from typing import Any

from agent.common.audit import (
    AUDIT_HUB_DIRECT_APPROVAL_REQUIRED,
    AUDIT_HUB_DIRECT_TOOL_BLOCKED,
    AUDIT_HUB_DIRECT_TOOL_COMPLETED,
    AUDIT_HUB_DIRECT_TOOL_REQUESTED,
    audit_hub_direct_event,
)
from agent.services.ananta_tool_policy_service import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    get_ananta_tool_policy_service,
)
from agent.services.ananta_tool_registry_service import (
    CATEGORY_CONTROLLED_WRITE,
    CATEGORY_READ_ONLY,
    get_ananta_tool_registry_service,
)
from agent.services.worker_runtime_execution_adapter import (
    WorkerRuntimeExecutionAdapter,
    get_worker_runtime_execution_adapter,
)

KIND_DIRECT_TOOL_RESULT = "direct_tool_result"
KIND_DIRECT_POLICY_BLOCKED = "direct_policy_blocked"
KIND_DIRECT_APPROVAL_REQUIRED = "direct_approval_required"

_RESULT_SCHEMA = "hub_direct_execution_result.v1"


def derive_mutation_mode(task: dict[str, Any] | None, agent_cfg: dict[str, Any] | None) -> str:
    """HDE-007: mutation mode from task kind / agent config, never a
    blanket ``controlled_workspace``."""
    mutation_cfg = (agent_cfg or {}).get("ananta_worker_workspace_mutation")
    mutation_cfg = dict(mutation_cfg) if isinstance(mutation_cfg, dict) else {}
    task_kind = str((task or {}).get("task_kind") or "").strip().lower()
    by_kind = mutation_cfg.get("mode_by_task_kind") if isinstance(mutation_cfg.get("mode_by_task_kind"), dict) else {}
    if task_kind and task_kind in by_kind:
        return str(by_kind[task_kind] or "read_only")
    return str(mutation_cfg.get("mutation_mode") or "read_only")


class HubToolExecutionAdapter:
    """Authorizes and dispatches one hub-direct tool call (HDE-006)."""

    def __init__(
        self,
        *,
        runtime_adapter: WorkerRuntimeExecutionAdapter | None = None,
        policy_service=None,
    ) -> None:
        self._runtime_adapter = runtime_adapter or get_worker_runtime_execution_adapter()
        self._policy_service = policy_service or get_ananta_tool_policy_service()

    def execute_direct(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        agent_cfg: dict[str, Any] | None,
        task: dict[str, Any] | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        workspace_ref: str | None = None,
        mutation_mode: str | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._direct_config(agent_cfg)
        name = str(tool_name or "").strip()
        args = dict(arguments or {})
        task_id = task_id or str((task or {}).get("id") or "") or None
        goal_id = goal_id or str((task or {}).get("goal_id") or "") or None
        mode = str(mutation_mode or derive_mutation_mode(task, agent_cfg))
        audit_enabled = bool(cfg.get("audit_enabled", True))

        if audit_enabled:
            audit_hub_direct_event(
                AUDIT_HUB_DIRECT_TOOL_REQUESTED,
                tool_name=name,
                task_id=task_id,
                goal_id=goal_id,
                reason_code=reason_code,
                mutation_mode=mode,
            )

        # HDE-007: every direct call passes the same policy gate the
        # worker tool loop uses; require_policy_gate=false is not a
        # bypass — the gate is mandatory, the flag only exists to make
        # the safe default explicit in config.
        policy = self._policy_service.evaluate(
            tool_name=name,
            arguments=args,
            allowed_tools=list(cfg.get("allowed_tools") or []),
            mutation_mode=mode,
            task_id=task_id,
            goal_id=goal_id,
        )

        if policy.decision == DECISION_APPROVAL_REQUIRED:
            return self._enter_approval_required(
                tool_name=name,
                arguments=args,
                policy=policy,
                task_id=task_id,
                goal_id=goal_id,
                agent_cfg=agent_cfg,
                audit_enabled=audit_enabled,
            )

        if policy.decision != DECISION_ALLOW:
            if audit_enabled:
                audit_hub_direct_event(
                    AUDIT_HUB_DIRECT_TOOL_BLOCKED,
                    tool_name=name,
                    policy_decision=policy.decision,
                    risk_class=policy.risk_class,
                    reason_code=policy.reason,
                    task_id=task_id,
                    goal_id=goal_id,
                )
            return {
                "schema": _RESULT_SCHEMA,
                "kind": KIND_DIRECT_POLICY_BLOCKED,
                "tool_name": name,
                "policy_decision": policy.as_dict(),
                "task_id": task_id,
                "goal_id": goal_id,
            }

        tool_call_id = f"hub-direct-{uuid.uuid4().hex[:12]}"
        tool_result = self._runtime_adapter.dispatch(
            tool_name=name,
            arguments=args,
            task_id=task_id,
            goal_id=goal_id,
            workspace_ref=workspace_ref,
            mutation_mode=mode,
            policy_decision=policy.as_dict(),
            tool_call_id=tool_call_id,
            config=self._runtime_config(agent_cfg, cfg),
            audit_enabled=audit_enabled,
        )

        status = str(tool_result.get("status") or "error")
        if status == "ok":
            self._consume_grant_if_any(tool_name=name, arguments=args, task_id=task_id, goal_id=goal_id)
        if audit_enabled:
            audit_hub_direct_event(
                AUDIT_HUB_DIRECT_TOOL_COMPLETED,
                tool_name=name,
                policy_decision=policy.decision,
                risk_class=policy.risk_class,
                status=status,
                task_id=task_id,
                goal_id=goal_id,
                detail=str(tool_result.get("error") or ""),
            )
        return {
            "schema": _RESULT_SCHEMA,
            "kind": KIND_DIRECT_TOOL_RESULT,
            "tool_name": name,
            "tool_result": tool_result,
            "policy_decision": policy.as_dict(),
            "task_id": task_id,
            "goal_id": goal_id,
        }

    # -- approval lifecycle (HDE-008) ---------------------------------------

    def _enter_approval_required(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        policy,
        task_id: str | None,
        goal_id: str | None,
        agent_cfg: dict[str, Any] | None,
        audit_enabled: bool,
    ) -> dict[str, Any]:
        request_id = None
        request_status = None
        try:
            from agent.services.approval_request_service import get_approval_request_service

            spec = get_ananta_tool_registry_service().get_tool(tool_name)
            approval_class = None
            if spec is not None and spec.category == CATEGORY_READ_ONLY:
                approval_class = "read_only"
            elif spec is not None and spec.category == CATEGORY_CONTROLLED_WRITE:
                approval_class = "controlled_workspace_writes"
            request = get_approval_request_service().create_pending_request(
                task_id=task_id,
                goal_id=goal_id,
                tool_name=tool_name,
                arguments=arguments,
                risk_class=policy.risk_class,
                scope={
                    "source": "hub_direct_execution",
                    **({"approval_class": approval_class} if approval_class else {}),
                },
                agent_cfg=agent_cfg,
            )
            request_id = request.id
            request_status = request.status
        except Exception:
            request_id = None

        if audit_enabled:
            audit_hub_direct_event(
                AUDIT_HUB_DIRECT_APPROVAL_REQUIRED,
                tool_name=tool_name,
                policy_decision=policy.decision,
                risk_class=policy.risk_class,
                reason_code=policy.reason,
                task_id=task_id,
                goal_id=goal_id,
                approval_request_id=request_id,
            )
        return {
            "schema": _RESULT_SCHEMA,
            "kind": KIND_DIRECT_APPROVAL_REQUIRED,
            "tool_name": tool_name,
            "policy_decision": policy.as_dict(),
            "approval_request_id": request_id,
            "approval_request_status": request_status,
            "task_id": task_id,
            "goal_id": goal_id,
        }

    @staticmethod
    def _consume_grant_if_any(
        *, tool_name: str, arguments: dict[str, Any], task_id: str | None, goal_id: str | None
    ) -> None:
        """One-shot grants are consumed after successful execution.

        Grants are digest-bound: ``resolve_grant_for_call`` recomputes
        the digest from the actual arguments, so a grant for different
        arguments never matches (HDE-008).
        """
        try:
            from agent.services.approval_request_service import get_approval_request_service

            svc = get_approval_request_service()
            grant = svc.resolve_grant_for_call(
                tool_name=tool_name, arguments=arguments, task_id=task_id, goal_id=goal_id
            )
            if grant is not None and bool(svc.get_lifecycle_config().get("grant_one_shot", True)):
                svc.consume_request(grant.id)
        except Exception:
            return

    # -- config helpers ------------------------------------------------------

    @staticmethod
    def _direct_config(agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = (agent_cfg or {}).get("hub_direct_execution")
        return dict(cfg) if isinstance(cfg, dict) else {}

    @staticmethod
    def _runtime_config(agent_cfg: dict[str, Any] | None, direct_cfg: dict[str, Any]) -> dict[str, Any]:
        """Forward only the execution-relevant config blocks to the runtime."""
        cfg = dict(agent_cfg or {})
        runtime_config: dict[str, Any] = {
            "max_result_chars": int(direct_cfg.get("max_result_chars") or 8000),
        }
        for block in ("ananta_worker_workspace_mutation", "shell_command_policy", "ananta_worker_tool_loop", "worker_runtime"):
            if isinstance(cfg.get(block), dict):
                runtime_config[block] = dict(cfg[block])
        if direct_cfg.get("env_allowlist"):
            runtime_config["env_allowlist"] = list(direct_cfg.get("env_allowlist") or [])
        return runtime_config


hub_tool_execution_adapter = HubToolExecutionAdapter()


def get_hub_tool_execution_adapter() -> HubToolExecutionAdapter:
    return hub_tool_execution_adapter
