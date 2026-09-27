"""Path A: the Hub runs a CodeCompass tool for a delegated worker (WCRB-008).

Under ``codecompass_access = "hub"`` a worker's tool loop does not open
CodeCompass itself; it asks the Hub to run the tool for the task it was
assigned. The Hub trusts nothing the worker says about authority and admits
the call only when all of the following hold (each fail closed):

1. the task exists, is not finished, and is assigned to the calling worker;
2. the tool is a CodeCompass tool on the Hub's worker allowlist, registered as
   a read-only operation;
3. the task's requester roles (``requested_roles``, inherited from parents)
   allow the operation; work without requester roles is unrestricted, exactly
   as its delegated capability;
4. a capability for that requester, bound to the task, can be issued.

The tool then runs on the Hub under that capability, bounded and audited.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent.services.hub_tool_gateway import audit_tool_call, run_bounded, timeout_seconds

TOOL_PREFIX = "codecompass."
MAX_RESULT_BYTES = 256 * 1024
TIMEOUT_ENV = "ANANTA_WORKER_TOOL_GATEWAY_TIMEOUT_SECONDS"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FINISHED = frozenset({"completed", "failed", "cancelled", "verification_failed", "skipped", "aborted",
                       "timeout", "archived"})


class GatewayDenied(Exception):
    def __init__(self, reason: str, status_code: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _same_worker(assigned: Any, caller: str) -> bool:
    assigned = str(assigned or "").rstrip("/")
    return bool(assigned) and assigned == str(caller or "").rstrip("/")


class WorkerToolGateway:
    def __init__(self, *, get_task: Callable[[str], Any], role_grants: Callable[[], Mapping[str, Any]],
                 registry: Any, allowed_tools: Callable[[], list[str]],
                 issue_capability: Callable[..., dict[str, Any] | None],
                 execute_tool: Callable[..., dict[str, Any]]) -> None:
        self._get_task = get_task
        self._role_grants = role_grants
        self._registry = registry
        self._allowed_tools = allowed_tools
        self._issue_capability = issue_capability
        self._execute_tool = execute_tool

    def admit(self, task_id: str, tool_name: str, *, worker_url: str) -> tuple[Any, dict[str, Any]]:
        """``(task, capability)`` for an admitted call; ``GatewayDenied`` otherwise."""
        from agent.services.operation_registry_service import ananta_tool_operation_id
        from agent.services.task_requester import requester_grants, resolve_requester

        task = self._get_task(task_id)
        if task is None:
            raise GatewayDenied("task_not_found", 404)
        if not _same_worker(getattr(task, "assigned_agent_url", None), worker_url):
            raise GatewayDenied("task_not_assigned_to_worker")
        if str(getattr(task, "status", "") or "").lower() in _FINISHED:
            raise GatewayDenied("task_not_active", 409)
        if not tool_name.startswith(TOOL_PREFIX) or tool_name not in self._allowed_tools():
            raise GatewayDenied("tool_not_allowed")
        operation = ananta_tool_operation_id(tool_name)
        descriptor = self._registry.get(operation)
        if descriptor is None or descriptor.access_class != "read" or descriptor.side_effecting:
            raise GatewayDenied("tool_not_read_only")
        role_grants = self._role_grants()
        requester = resolve_requester(task, self._get_task)
        grants = requester_grants(requester, role_grants) if requester is not None else None
        if grants is not None:  # no requester roles: unrestricted, exactly as the issued capability
            allowed, reason = grants.allows(operation, self._registry.groups_for(operation))
            if not allowed:
                raise GatewayDenied(f"role_denied:{reason}")
        capability = self._issue_capability(task, audience="hub", get_task=self._get_task, role_grants=role_grants)
        if capability is None:
            raise GatewayDenied("capability_unavailable", 503)
        return task, capability

    def execute(self, tool_name: str, arguments: Mapping[str, Any], *, tool_call_id: str,
                capability: dict[str, Any]) -> dict[str, Any]:
        """The tool's result, run locally on the Hub under ``capability``."""
        result = self._execute_tool(tool_name=tool_name, arguments=dict(arguments), workspace_dir=str(_REPO_ROOT),
                                    tool_call_id=tool_call_id,
                                    config={"codecompass_capability": capability, "codecompass_access": "delegated"})
        if len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")) > MAX_RESULT_BYTES:
            from agent.services.tools._evidence import build_tool_result

            return build_tool_result(tool_name=tool_name, tool_call_id=tool_call_id, status="error",
                                     error="hub_gateway_result_too_large")
        return result

    def call(self, app: Any, task_id: str, tool_name: str, arguments: Mapping[str, Any], *, worker_url: str,
             tool_call_id: str, trace_id: str | None) -> dict[str, Any]:
        """Admit, run bounded, audit. ``GatewayDenied`` for a refused call."""
        details = {"task_id": task_id, "worker_url": worker_url, "arguments_keys": sorted(arguments)}

        def audit(outcome: str, **extra: Any) -> None:
            audit_tool_call("worker_tool_gateway_called", tool_name, outcome=outcome, trace_id=trace_id,
                            target_scope={"task_id": task_id}, actor_role="worker", details={**details, **extra})

        try:
            _task, capability = self.admit(task_id, tool_name, worker_url=worker_url)
        except GatewayDenied as denied:
            audit("blocked", reason=denied.reason)
            raise
        from agent.services.hub_tool_gateway import GatewayTimeout

        try:
            result = run_bounded(app, lambda: self.execute(tool_name, arguments, tool_call_id=tool_call_id,
                                                           capability=capability), timeout_seconds(TIMEOUT_ENV, 45.0))
        except GatewayTimeout:
            audit("timeout")
            raise GatewayDenied("tool_timeout", 504) from None
        audit("success" if result.get("status") != "error" else "error", status=str(result.get("status") or ""))
        return result


def get_worker_tool_gateway() -> WorkerToolGateway:
    from agent.cli_backends.tool_loop import get_tool_loop_config
    from agent.repository import task_repo
    from agent.services.access_role_admin_service import get_access_role_admin_service
    from agent.services.codecompass_task_capability import issue_task_capability
    from agent.services.operation_registry_service import get_operation_registry_service
    from agent.services.tools import execute_ananta_tool

    return WorkerToolGateway(
        get_task=task_repo.get_by_id,
        role_grants=get_access_role_admin_service().role_grants,
        registry=get_operation_registry_service(),
        allowed_tools=lambda: list(get_tool_loop_config().get("allowed_tools") or []),
        issue_capability=issue_task_capability,
        execute_tool=execute_ananta_tool,
    )
