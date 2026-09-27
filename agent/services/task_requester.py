"""Who asked for a task, recorded at ingestion so its tools never get more than that (WCRB-006).

At ingestion the requester is what the caller stated explicitly, or else the
authenticated user of the current request (subject, tenant and bound access
roles). A task derived from another one resolves its requester at use time by
walking up its parents (``resolve_requester``). Tasks without any (system
work, older tasks) keep the previous behaviour: no role restriction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

REQUESTER_FIELDS = ("requested_by_subject", "requested_by_tenant", "requested_roles")


def _from_request() -> dict[str, Any]:
    try:
        from flask import g, has_request_context
    except ImportError:  # pragma: no cover - flask is always installed in the hub
        return {}
    if not has_request_context() or not getattr(g, "user", None):
        return {}
    from agent.auth import get_current_principal

    principal = get_current_principal()
    if not principal.subject_id:
        return {}
    return {"requested_by_subject": principal.subject_id, "requested_by_tenant": principal.tenant_id,
            "requested_roles": sorted(principal.roles)}


def requester_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Requester fields from the authenticated request; empty when stated by the caller or not a user request.

    Nothing is read from the database here: ingestion must not add reads between a caller's reservation and
    the insert. Derived tasks get their requester at use time (``resolve_requester``).
    """
    if fields.get("requested_by_subject"):
        return {}
    return _from_request()


MAX_PARENT_DEPTH = 16


def resolve_requester(task: Any, get_task: Callable[[str], Any]) -> Any:
    """The task that carries the requester: ``task`` itself or its nearest ancestor with one (bounded)."""
    current, seen = task, set()
    for _ in range(MAX_PARENT_DEPTH):
        if current is None:
            return None
        if getattr(current, "requested_by_subject", None):
            return current
        parent_id = str(getattr(current, "parent_task_id", None) or getattr(current, "source_task_id", None) or "")
        if not parent_id or parent_id in seen:
            return None
        seen.add(parent_id)
        current = get_task(parent_id)
    return None


def requester_grants(task: Any, roles: Mapping[str, Mapping[str, Any]]) -> Any:
    """The effective grants of a task's requester roles, or ``None`` when the task has no requester roles."""
    from agent.services.access_roles import EffectiveGrants, effective_grants

    role_ids = [str(role) for role in (getattr(task, "requested_roles", None) or []) if str(role).strip()]
    if not role_ids:
        return None
    grants = effective_grants(roles, role_ids)
    # roles deleted since the task was created must not lift the restriction: no known role grants nothing
    return grants if grants.roles else EffectiveGrants(roles=frozenset(role_ids))
