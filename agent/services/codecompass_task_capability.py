"""A task's CodeCompass capability on its way from the Hub to the worker's tool loop (WCRB-009).

Delegated access (``ananta_worker_tool_loop.codecompass_access = "delegated"``,
the default): when the Hub forwards a task step to a worker it issues a signed
capability for the task's requester, bound to the task and that worker, and
sends it with the step. The worker keeps it in memory for exactly that step
(a context variable, never a workspace file a model could read) and the tool
loop hands it to the CodeCompass tools.

Only a worker accepts a capability, and only from a service-authenticated
request (the Hub); a capability in any other request is ignored, and the Hub
always issues its own. When the Hub runs a task itself, the tool loop issues
one directly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any

ACCESS_MODES = ("delegated", "hub", "off")
DEFAULT_ACCESS_MODE = "delegated"
_CURRENT: ContextVar[dict[str, Any] | None] = ContextVar("codecompass_task_capability", default=None)
_log = logging.getLogger(__name__)


def _mode(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    return value if value in ACCESS_MODES else None


def access_mode(tool_loop_config: Mapping[str, Any] | None, tool_name: str | None = None) -> str:
    """The configured access path, globally or for ``tool_name`` (``codecompass_access_overrides``)."""
    cfg = tool_loop_config or {}
    overrides = cfg.get("codecompass_access_overrides")
    if tool_name and isinstance(overrides, Mapping) and _mode(overrides.get(tool_name)):
        return _mode(overrides.get(tool_name)) or DEFAULT_ACCESS_MODE
    return _mode(cfg.get("codecompass_access")) or DEFAULT_ACCESS_MODE


def uses_delegation(tool_loop_config: Mapping[str, Any] | None) -> bool:
    """Whether any tool runs delegated, so the step needs a capability."""
    cfg = tool_loop_config or {}
    overrides = cfg.get("codecompass_access_overrides")
    modes = {access_mode(cfg)} | {_mode(value) for value in dict(overrides).values()} if isinstance(
        overrides, Mapping) else {access_mode(cfg)}
    return "delegated" in modes


def effective_access_mode(tool_loop_config: Mapping[str, Any] | None, tool_name: str, *,
                          has_capability: bool) -> str:
    """The path of one call: delegated without a capability falls back to the Hub only when configured."""
    mode = access_mode(tool_loop_config, tool_name)
    fallback = _mode((tool_loop_config or {}).get("codecompass_access_fallback"))
    if mode == "delegated" and not has_capability and fallback == "hub":
        return "hub"
    return mode


def accepted_capability(value: Any, *, role: str, service_authenticated: bool) -> dict[str, Any] | None:
    """The capability a request may bring: on a worker, from the Hub only; nowhere else."""
    if role != "worker" or not service_authenticated or not isinstance(value, Mapping):
        return None
    return dict(value)


@contextmanager
def task_capability_scope(capability: dict[str, Any] | None) -> Iterator[None]:
    token = _CURRENT.set(capability)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_task_capability() -> dict[str, Any] | None:
    return _CURRENT.get()


def _as_task(task: Any) -> Any:
    return SimpleNamespace(**task) if isinstance(task, Mapping) else task


def issue_task_capability(task: Any, *, audience: str, get_task: Any = None, role_grants: Any = None
                          ) -> dict[str, Any] | None:
    """A signed capability for ``task``'s requester (or unrestricted system work), or ``None`` (fail closed)."""
    from agent.services.codecompass_capability_issuer import CapabilityError, issue_capability
    from agent.services.task_requester import requester_grants, resolve_requester

    task = _as_task(task)
    task_id = str(getattr(task, "id", "") or "")
    if get_task is None:
        from agent.repository import task_repo

        get_task = task_repo.get_by_id
    if role_grants is None:
        from agent.services.access_role_admin_service import get_access_role_admin_service

        role_grants = get_access_role_admin_service().role_grants()
    requester = resolve_requester(task, get_task)
    subject = str(getattr(requester, "requested_by_subject", "") or "") or f"system:{task_id or 'task'}"
    tenant = (getattr(requester, "requested_by_tenant", None) if requester is not None else None) or getattr(
        task, "tenant_id", None)
    grants = requester_grants(requester, role_grants) if requester is not None else None
    try:
        return issue_capability(subject_id=subject, tenant_id=tenant, task_id=task_id, audience=audience,
                                grants=grants)
    except CapabilityError as error:
        _log.warning("codecompass capability not issued for task %s: %s", task_id, error)
        return None
