"""Principal ownership of Run-Control resources (task / goal / run).

Owns the in-memory ownership index and its atomic authorize/bind rules.  The
guarding lock is supplied by the owning service so that ownership checks and
idempotency reservation keep sharing one critical section.
"""
from __future__ import annotations

from typing import Any, Callable, ContextManager

from agent.config import settings
from agent.services.run_control_models import RunControlPrincipal

ResourceKey = tuple[str, str]


def run_control_resource_keys(
    *,
    task_id: str | None,
    goal_id: str | None,
    run_id: str | None,
) -> tuple[ResourceKey, ...]:
    return tuple(
        (kind, str(value))
        for kind, value in (("task", task_id), ("goal", goal_id), ("run", run_id))
        if value
    )


def resolve_legacy_resource_principal(
    resource_key: ResourceKey,
) -> RunControlPrincipal | None:
    """Resolve pre-tenancy rows without a caller-wins ownership claim.

    Historic Hub tasks/goals contain a subject but no organization.  The
    only deterministic compatible principal is therefore ``(subject,
    subject)``, matching local Hub accounts.  Externally tenanted callers
    must arrive through a trusted adapter which verifies and explicitly
    binds the resource first.
    """

    kind, resource_id = resource_key
    try:
        from agent.services.repository_registry import get_repository_registry

        repositories = get_repository_registry()
        record = (
            repositories.task_repo.get_by_id(resource_id)
            if kind == "task"
            else repositories.goal_repo.get_by_id(resource_id)
        )
    except Exception:
        return None
    if record is None:
        return None
    if kind == "task":
        ingest = next(
            (
                event
                for event in list(getattr(record, "history", None) or [])
                if isinstance(event, dict) and event.get("event_type") == "task_ingested"
            ),
            None,
        )
        actor = str((ingest or {}).get("actor") or "").strip()
    else:
        actor = str(getattr(record, "requested_by", "") or "").strip()
    if actor and actor not in {"system", "hub", "unknown", "operator"}:
        subject = actor
    else:
        subject = str(settings.initial_admin_user or "").strip()
    try:
        return RunControlPrincipal.from_values(subject, subject)
    except ValueError:
        return None


class RunControlResourceOwnership:
    """Atomically authorize and bind resource owners."""

    def __init__(
        self,
        *,
        lock_provider: Callable[[], ContextManager[Any]],
        legacy_principal_resolver: Callable[
            [ResourceKey], RunControlPrincipal | None
        ] = resolve_legacy_resource_principal,
    ) -> None:
        self._lock_provider = lock_provider
        self._legacy_principal_resolver = legacy_principal_resolver
        self.owners: dict[ResourceKey, RunControlPrincipal] = {}

    def authorize_resources(
        self,
        *,
        principal: RunControlPrincipal,
        task_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        allow_legacy_binding: bool = True,
    ) -> bool:
        """Atomically authorize all exact resources and migrate legacy tasks."""

        keys = run_control_resource_keys(task_id=task_id, goal_id=goal_id, run_id=run_id)
        if not keys:
            return False
        with self._lock_provider():
            resolved: dict[ResourceKey, RunControlPrincipal] = {}
            task_key = ("task", str(task_id)) if task_id else None
            for resource_key in keys:
                owner = self.owners.get(resource_key)
                if owner is None and resource_key[0] == "run" and task_key is not None:
                    owner = self.owners.get(task_key) or resolved.get(task_key)
                if owner is None and allow_legacy_binding:
                    if resource_key[0] in {"task", "goal"}:
                        owner = self._legacy_principal_resolver(resource_key)
                    # A standalone historic run has no durable owner source.
                    # It may inherit an already verified task binding above,
                    # otherwise only a trusted adapter may bind it explicitly.
                if owner != principal:
                    return False
                resolved[resource_key] = owner
            for resource_key in keys:
                self.owners.setdefault(resource_key, principal)
            return True

    def bind_resource_owners(
        self,
        *,
        principal: RunControlPrincipal,
        resources: tuple[tuple[str, str], ...],
    ) -> bool:
        """Atomically bind resources verified by a trusted Hub adapter."""

        keys = tuple((str(kind), str(resource_id)) for kind, resource_id in resources)
        if not keys or any(kind not in {"task", "goal", "run"} or not resource_id for kind, resource_id in keys):
            return False
        with self._lock_provider():
            if any(
                existing is not None and existing != principal
                for existing in (self.owners.get(key) for key in keys)
            ):
                return False
            for key in keys:
                self.owners[key] = principal
            return True
