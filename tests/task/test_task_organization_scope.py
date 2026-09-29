"""Where a derived task belongs.

The schema ties tenant, project, organisation, unit, team and role slot
together through composite foreign keys, so what is pinned here is that the
organisation group travels whole or not at all, and that a caller who states
part of the scope is never silently completed from somewhere else.
"""

from __future__ import annotations

import pytest

from agent.services.task_organization_scope import (
    ORGANIZATION_SCOPE_ALL,
    inherited_organization_scope,
    organization_scope_of,
    parent_reference,
    resolve_ingest_scope,
    states_any_scope,
)


class _Task:
    """A task as the repositories hand one over."""

    def __init__(self, **values: object) -> None:
        for field in ORGANIZATION_SCOPE_ALL:
            setattr(self, field, values.get(field))


def _organized(**overrides: str) -> dict[str, str]:
    scope = {
        "tenant_id": "t-1",
        "project_id": "p-1",
        "organization_id": "org-1",
        "unit_id": "unit-1",
        "team_id": "team-1",
        "role_slot_id": "slot-1",
    }
    scope.update(overrides)
    return scope


def test_a_task_reports_the_scope_it_carries() -> None:
    assert organization_scope_of(_organized()) == _organized()


def test_a_scope_leaves_out_what_the_task_does_not_have() -> None:
    scope = organization_scope_of({"tenant_id": "t-1", "organization_id": None, "team_id": "  "})

    assert scope == {"tenant_id": "t-1"}


def test_a_scope_is_read_off_a_model_as_readily_as_a_mapping() -> None:
    assert organization_scope_of(_Task(**_organized())) == _organized()


def test_a_task_that_is_no_task_at_all_carries_no_scope() -> None:
    assert organization_scope_of(None) == {}
    assert organization_scope_of(object()) == {}


@pytest.mark.parametrize("value", [None, 12, "", "   ", "x" * 192])
def test_a_value_that_could_not_be_an_identifier_is_dropped(value: object) -> None:
    """A truncated identifier points at nothing; a foreign key would refuse it."""

    assert organization_scope_of({"organization_id": value}) == {}


def test_a_value_at_the_column_limit_is_still_an_identifier() -> None:
    identifier = "x" * 191

    assert organization_scope_of({"organization_id": identifier}) == {"organization_id": identifier}


def test_a_derived_task_inherits_the_whole_organisation_tuple() -> None:
    """The parent's tuple already passed the composite keys; a copy passes too."""

    assert inherited_organization_scope(_organized(), {}) == _organized()


def test_the_organisation_group_never_travels_without_its_organisation() -> None:
    """Half a tuple references a combination the schema never approved."""

    parent = {"tenant_id": "t-1", "project_id": "p-1", "unit_id": "unit-1", "team_id": "team-1"}

    assert inherited_organization_scope(parent, {}) == {"tenant_id": "t-1", "project_id": "p-1"}


def test_tenant_and_project_travel_even_without_an_organisation() -> None:
    parent = {"tenant_id": "t-1", "project_id": "p-1"}

    assert inherited_organization_scope(parent, {}) == parent


def test_a_caller_that_states_part_of_the_scope_is_taken_at_its_word_for_all_of_it() -> None:
    """A stated project beside an inherited organisation is a dangling tuple."""

    assert inherited_organization_scope(_organized(), {"project_id": "p-2"}) == {}
    assert inherited_organization_scope(_organized(), {"team_id": "team-9"}) == {}


def test_a_caller_stating_nothing_relevant_still_inherits() -> None:
    payload = {"title": "Etwas", "priority": "high", "team_id": ""}

    assert inherited_organization_scope(_organized(), payload) == _organized()


def test_a_parentless_task_inherits_nothing() -> None:
    assert inherited_organization_scope(None, {}) == {}
    assert inherited_organization_scope({}, {}) == {}


def test_a_partial_parent_passes_on_only_what_it_has() -> None:
    parent = {"tenant_id": "t-1", "project_id": "p-1", "organization_id": "org-1"}

    assert inherited_organization_scope(parent, {}) == parent


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        ({"parent_task_id": "p-1", "source_task_id": "s-1"}, "p-1"),
        ({"source_task_id": "s-1"}, "s-1"),
        ({"parent_task_id": "   ", "source_task_id": "s-1"}, "s-1"),
        ({}, ""),
        (None, ""),
    ),
)
def test_scope_is_inherited_one_step_at_a_time(payload: dict | None, expected: str) -> None:
    """The immediate parent wins: a task sits where the thing that made it sits."""

    assert parent_reference(payload) == expected


def test_a_caller_is_recognised_as_having_stated_a_scope() -> None:
    assert states_any_scope({"organization_id": "org-1"}) is True
    assert states_any_scope({"title": "Etwas"}) is False
    assert states_any_scope(None) is False


def test_a_team_passed_alongside_an_inherited_one_agrees_and_keeps_the_tuple() -> None:
    """Callers that derive pass the parent's team; that is the same claim."""

    scope, team = resolve_ingest_scope(_organized(), {}, "team-1")

    assert team == "team-1"
    assert scope == {
        "tenant_id": "t-1",
        "project_id": "p-1",
        "organization_id": "org-1",
        "unit_id": "unit-1",
        "role_slot_id": "slot-1",
    }


def test_a_stated_team_wins_but_takes_the_organisation_group_down_with_it() -> None:
    """That team beside that organisation is a pair no key ever approved."""

    scope, team = resolve_ingest_scope(_organized(), {}, "team-other")

    assert team == "team-other"
    assert scope == {"tenant_id": "t-1", "project_id": "p-1"}


def test_no_stated_team_takes_the_inherited_one() -> None:
    scope, team = resolve_ingest_scope(_organized(), {}, None)

    assert team == "team-1"
    assert "team_id" not in scope


def test_a_stated_team_survives_a_parent_with_nothing_to_give() -> None:
    scope, team = resolve_ingest_scope(None, {}, "team-9")

    assert (scope, team) == ({}, "team-9")


def test_ingesting_without_any_team_at_all_states_none() -> None:
    assert resolve_ingest_scope(None, {}, "  ") == ({}, None)


def test_a_parent_outside_an_organisation_passes_on_only_tenant_and_project() -> None:
    parent = {"tenant_id": "t-1", "project_id": "p-1", "team_id": "team-1"}

    scope, team = resolve_ingest_scope(parent, {}, None)

    assert scope == {"tenant_id": "t-1", "project_id": "p-1"}
    assert team is None
