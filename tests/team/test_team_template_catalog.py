"""One selectable list of every template that exists.

Templates answer two different questions — who works together, and how the
work flows — and used to live in three places. What this pins is that all of
them stay selectable, that a source going dark costs only its own entries, and
that every role a template asks for arrives with a name a person can read.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.alias_catalog import default_alias_registry
from agent.services.team_template_catalog import (
    TEAM_TEMPLATE_KIND_PROCESS,
    TEAM_TEMPLATE_KIND_TEAM,
    TeamTemplate,
    TeamTemplateCatalog,
    TeamTemplateCatalogError,
    TeamTemplateRole,
)


class _Row:
    def __init__(self, identifier: str, name: str, description: str = "") -> None:
        self.id = identifier
        self.name = name
        self.description = description


class _TeamTypes:
    """A read port over in-memory rows, shaped like the repositories."""

    def __init__(
        self,
        types: tuple[_Row, ...] = (),
        roles: tuple[_Row, ...] = (),
        links: dict[str, tuple[str, ...]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._types = types
        self._roles = roles
        self._links = links or {}
        self._error = error

    def team_types(self) -> tuple[_Row, ...]:
        if self._error is not None:
            raise self._error
        return self._types

    def role_ids_for(self, team_type_id: str) -> tuple[str, ...]:
        return self._links.get(team_type_id, ())

    def roles(self) -> tuple[_Row, ...]:
        return self._roles


_PRESETS = [
    {"id": "preset-tdd-loop", "name": "TDD Loop", "description": "ignored in favour of the alias text"},
    {"id": "preset-code-review", "name": "Code Review Pipeline", "description": ""},
]


def _catalog(team_types: Any = None, presets: Any = None) -> TeamTemplateCatalog:
    return TeamTemplateCatalog(
        team_types=team_types,
        presets=_PRESETS if presets is None else presets,
        aliases=default_alias_registry(),
    )


def _scrum() -> _TeamTypes:
    return _TeamTypes(
        types=(_Row("tt-1", "Scrum", "Ein Standardteam"),),
        roles=(_Row("r-1", "Product Owner"), _Row("r-2", "Scrum Master"), _Row("r-3", "Developer")),
        links={"tt-1": ("r-1", "r-2", "r-3")},
    )


def test_both_kinds_of_template_are_offered_in_one_list() -> None:
    templates = _catalog(_scrum()).list()

    kinds = {template.kind for template in templates}
    assert kinds == {TEAM_TEMPLATE_KIND_TEAM, TEAM_TEMPLATE_KIND_PROCESS}
    assert len(templates) == 3


def test_teams_come_first_because_that_is_the_first_decision() -> None:
    templates = _catalog(_scrum()).list()

    assert templates[0].kind == TEAM_TEMPLATE_KIND_TEAM


def test_a_team_template_carries_its_roles_by_name_and_by_identity() -> None:
    template = next(t for t in _catalog(_scrum()).list() if t.kind == TEAM_TEMPLATE_KIND_TEAM)

    assert template.agent_count == 3
    assert [role.display_name for role in template.roles] == ["Product Owner", "Scrum Master", "Developer"]
    assert [role.role_id for role in template.roles] == ["r-1", "r-2", "r-3"]


def test_a_process_template_uses_the_readable_name_over_the_technical_one() -> None:
    templates = {t.source_id: t for t in _catalog().list()}

    assert templates["preset-tdd-loop"].display_name == "Erst Test, dann Code"
    assert "TDD" in templates["preset-tdd-loop"].aliases


def test_a_source_going_dark_costs_only_its_own_entries() -> None:
    """A person can still pick a process template when the database is away."""

    catalog = _catalog(_TeamTypes(error=RuntimeError("database unavailable")))

    templates = catalog.list()

    assert [t.kind for t in templates] == [TEAM_TEMPLATE_KIND_PROCESS] * 2


def test_a_catalog_without_a_database_still_offers_process_templates() -> None:
    assert len(_catalog(None).list()) == 2


def test_template_ids_are_namespaced_so_two_sources_cannot_collide() -> None:
    templates = _catalog(_scrum()).list()

    assert {t.template_id for t in templates} == {
        "team:tt-1",
        "process:preset-tdd-loop",
        "process:preset-code-review",
    }


def test_a_role_the_database_no_longer_names_still_renders() -> None:
    """A dangling link degrades to the identifier, never to a blank."""

    types = _TeamTypes(
        types=(_Row("tt-1", "Scrum"),),
        roles=(),
        links={"tt-1": ("r-missing",)},
    )

    template = next(t for t in _catalog(types).list() if t.kind == TEAM_TEMPLATE_KIND_TEAM)

    assert template.roles[0].display_name == "r-missing"


def test_templates_are_listed_in_a_stable_readable_order() -> None:
    types = _TeamTypes(
        types=(_Row("tt-2", "Zebra"), _Row("tt-1", "Alpha")),
        roles=(),
        links={},
    )

    names = [t.display_name for t in _catalog(types).list() if t.kind == TEAM_TEMPLATE_KIND_TEAM]

    assert names == ["Alpha", "Zebra"]


def test_the_catalog_serialises_with_the_counts_a_chooser_needs() -> None:
    payload = _catalog(_scrum()).to_dict()

    team = next(item for item in payload["templates"] if item["kind"] == TEAM_TEMPLATE_KIND_TEAM)
    assert team["agent_count"] == 3
    assert team["roles"][0]["display_name"] == "Product Owner"
    assert payload["schema"] == "ananta.team_template_catalog.v1"


@pytest.mark.parametrize(
    ("field", "value"),
    (("kind", "something-else"), ("template_id", ""), ("source_id", " "), ("display_name", "")),
)
def test_a_malformed_template_fails_closed(field: str, value: str) -> None:
    values: dict[str, Any] = {
        "template_id": "team:tt-1",
        "kind": TEAM_TEMPLATE_KIND_TEAM,
        "source_id": "tt-1",
        "display_name": "Scrum",
        "description": "",
        "roles": (TeamTemplateRole("r-1", "Product Owner"),),
        "aliases": (),
    }
    values[field] = value

    with pytest.raises(TeamTemplateCatalogError):
        TeamTemplate(**values)


def test_a_read_port_of_the_wrong_shape_is_refused() -> None:
    with pytest.raises(TeamTemplateCatalogError, match="read_port_invalid"):
        TeamTemplateCatalog(team_types=object(), presets=(), aliases=default_alias_registry())
