"""Shared mechanics of the Hub tool gateways (WCRB-008).

Both gateway surfaces -- the Meet companion (``/internal/assist/tool``) and
delegated workers under ``codecompass_access = "hub"``
(``/api/worker/v1/tasks/<task>/tools/<name>``) -- run a tool on the Hub for a
caller that cannot run it itself. What they share lives here: a wall-clock
bound on a small executor, inside the application context, and the dual audit
(audit log + execution audit trail). Admission (who may call what) stays with
each surface, since their callers are authenticated differently.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import copy_context
from typing import Any, TypeVar

DEFAULT_TIMEOUT_SECONDS = 15.0
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hub-tool-gateway")
_T = TypeVar("_T")


class GatewayTimeout(Exception):
    """The tool did not finish within the gateway's wall-clock bound."""


def timeout_seconds(env_name: str, default: float = DEFAULT_TIMEOUT_SECONDS, *, environ=None) -> float:
    """A bounded timeout (1..60 s) from ``env_name``."""
    raw = (os.environ if environ is None else environ).get(env_name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(1.0, min(60.0, value))


def run_bounded(app: Any, work: Callable[[], _T], timeout: float) -> _T:
    """``work()`` in ``app``'s context on the gateway executor; ``GatewayTimeout`` after ``timeout`` seconds."""
    context = copy_context()  # e.g. the call's trusted capability scope

    def run() -> _T:
        with app.app_context():
            return work()

    try:
        return _EXECUTOR.submit(context.run, run).result(timeout=timeout)
    except FutureTimeout:
        raise GatewayTimeout() from None


def audit_tool_call(event: str, name: str, *, outcome: str, trace_id: str | None, target_scope: dict[str, Any],
                    actor_role: str, details: dict[str, Any]) -> None:
    from agent.common.audit import log_audit
    from agent.services.execution_audit_service import get_execution_audit_service

    log_audit(event, {"tool": name, "outcome": outcome, "trace_id": trace_id, **details})
    get_execution_audit_service().emit_tool_call(
        trace_id=trace_id,
        parent_trace_id=None,
        tool_name=name,
        target_scope=target_scope,
        outcome=outcome,
        actor_role=actor_role,
        details=details,
    )
