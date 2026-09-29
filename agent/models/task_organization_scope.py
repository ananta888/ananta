"""Carrying a task's place in the organisation to the tasks it produces.

A task row has columns for where it sits — tenant, project, organisation,
unit, team and role slot — and the schema enforces them together through
composite foreign keys.  What was missing is the moment they get written: a
task derived from another one kept its parent's team and lost everything
else, so the organisation had structure but no visible work, and the views
that ask "who is busy here" could only ever answer "nobody".

Inheritance is the safe way to fill them.  The parent's tuple already passed
those foreign keys, so copying it whole yields a tuple that passes them too.
Copying half of it would not: every organisation-scoped key references
(tenant, project, organisation, X), so the group travels together or not at
all.

Nothing here overwrites what a caller stated.  A caller that names any part
of the scope itself is taken at its word for all of it, because mixing a
stated project with an inherited organisation is exactly how a task ends up
pointing at a row that does not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# Scope that means the same thing with or without an organisation.
BASE_SCOPE_FIELDS: Final[tuple[str, ...]] = ("tenant_id", "project_id")

# Scope that only means anything relative to one organisation, and whose
# foreign keys all include the organisation in their tuple.
ORGANIZATION_SCOPE_FIELDS: Final[tuple[str, ...]] = (
    "organization_id",
    "unit_id",
    "team_id",
    "role_slot_id",
)

ORGANIZATION_SCOPE_ALL: Final[tuple[str, ...]] = (
    *BASE_SCOPE_FIELDS,
    *ORGANIZATION_SCOPE_FIELDS,
)

_MAX_SCOPE_VALUE = 191


def _as_mapping(task: Any) -> Mapping[str, Any]:
    if task is None:
        return {}
    if isinstance(task, Mapping):
        return task
    if hasattr(task, "model_dump"):
        try:
            dumped = task.model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return dumped
    return {field: getattr(task, field, None) for field in ORGANIZATION_SCOPE_ALL}


def _scope_value(value: Any) -> str:
    """One scope identifier, or empty when it is not usable as one.

    Values longer than the column are dropped rather than truncated: half an
    identifier points at nothing, and a foreign key would refuse it anyway.
    """

    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_SCOPE_VALUE:
        return ""
    return normalized


def organization_scope_of(task: Any) -> dict[str, str]:
    """The scope one task carries, leaving out what it does not."""

    payload = _as_mapping(task)
    scope: dict[str, str] = {}
    for field in ORGANIZATION_SCOPE_ALL:
        value = _scope_value(payload.get(field))
        if value:
            scope[field] = value
    return scope


def states_any_scope(payload: Mapping[str, Any] | None) -> bool:
    """Whether a caller already said where the new task belongs."""

    if not payload:
        return False
    return any(_scope_value(payload.get(field)) for field in ORGANIZATION_SCOPE_ALL)


def inherited_organization_scope(
    parent: Any,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """What a task derived from ``parent`` should carry.

    The organisation group travels whole and only when the parent is actually
    in an organisation; tenant and project travel on their own, because they
    mean the same thing to a task that belongs to no organisation at all.

    Returns nothing at all when the caller stated any part of the scope
    itself — a stated project beside an inherited organisation would point at
    a combination the foreign keys never approved.
    """

    if states_any_scope(payload):
        return {}
    parent_scope = organization_scope_of(parent)
    if not parent_scope:
        return {}
    inherited = {
        field: parent_scope[field]
        for field in BASE_SCOPE_FIELDS
        if field in parent_scope
    }
    if not parent_scope.get("organization_id"):
        return inherited
    for field in ORGANIZATION_SCOPE_FIELDS:
        if field in parent_scope:
            inherited[field] = parent_scope[field]
    return inherited


def resolve_ingest_scope(
    parent: Any,
    payload: Mapping[str, Any] | None,
    team_id: Any = None,
) -> tuple[dict[str, str], str | None]:
    """The scope a task is ingested with, and the team it lands in.

    Ingestion carries the team in its own argument, and callers that derive a
    task usually pass the parent's team through it.  That is the same claim
    inheritance would make, so the two agree by default and the tuple stays
    whole.

    When they disagree the stated team wins — a caller naming a team means it
    — but the organisation group is then dropped, because that team beside
    that organisation is a pair the foreign keys never approved.  Tenant and
    project survive: they hold regardless of which team does the work.
    """

    stated_team = _scope_value(team_id)
    inherited = inherited_organization_scope(parent, payload)
    if not inherited:
        return {}, (stated_team or None)

    inherited_team = inherited.get("team_id", "")
    if stated_team and inherited_team and stated_team != inherited_team:
        base = {field: inherited[field] for field in BASE_SCOPE_FIELDS if field in inherited}
        return base, stated_team

    scope = dict(inherited)
    team = scope.pop("team_id", "") or stated_team
    return scope, (team or None)


def parent_reference(payload: Mapping[str, Any] | None) -> str:
    """Which task a new one was derived from, if any.

    The immediate parent is preferred over the original source: scope is
    inherited one step at a time, so a task always sits where the thing that
    produced it sits.
    """

    if not payload:
        return ""
    for field in ("parent_task_id", "source_task_id"):
        value = _scope_value(payload.get(field))
        if value:
            return value
    return ""


def goal_reference(payload: Mapping[str, Any] | None) -> str:
    """Which goal a task was planned for, if any.

    A goal carries the same scope a task does, and it is where a chain of
    work starts: the first task has no parent to inherit from, so without
    this the organisation would only ever be filled in from the second task
    onward — which is to say, never.
    """

    if not payload:
        return ""
    return _scope_value(payload.get("goal_id"))


__all__ = [
    "BASE_SCOPE_FIELDS",
    "goal_reference",
    "resolve_ingest_scope",
    "ORGANIZATION_SCOPE_ALL",
    "ORGANIZATION_SCOPE_FIELDS",
    "inherited_organization_scope",
    "organization_scope_of",
    "parent_reference",
    "states_any_scope",
]
