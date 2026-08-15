"""The names this system actually ships.

A catalog that drifts from what it names is worse than none: a preset with no
entry silently falls back to its raw id, and an entry with no preset is a name
for something that no longer exists. Both are checked here against the real
registries rather than against a copy.
"""

from __future__ import annotations

import pytest

from agent.services.alias_catalog import (
    AGENT_ROLE_ALIASES,
    VISUAL_PROCESS_PRESET_ALIASES,
    WORKFLOW_RUNTIME_ALIASES,
    DatabaseRoleAliasSource,
    default_alias_registry,
)
from agent.services.alias_registry import (
    ALIAS_NAMESPACE_AGENT_ROLE,
    ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
    ALIAS_NAMESPACE_WORKFLOW_RUNTIME,
    AliasRegistry,
)
from agent.services.workflow_transition_outbox import TRANSITION_RUNTIMES
from agent.visual_process.presets import list_presets

_PRESET = ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET


def test_every_shipped_preset_has_a_human_name() -> None:
    """Otherwise the gallery shows a raw id to someone assembling a team."""

    shipped = {str(preset["id"]) for preset in list_presets()}

    assert shipped - set(VISUAL_PROCESS_PRESET_ALIASES) == set()


def test_no_alias_names_a_preset_that_no_longer_exists() -> None:
    shipped = {str(preset["id"]) for preset in list_presets()}

    assert set(VISUAL_PROCESS_PRESET_ALIASES) - shipped == set()


def test_every_transition_runtime_has_a_human_name() -> None:
    assert set(TRANSITION_RUNTIMES) - set(WORKFLOW_RUNTIME_ALIASES) == set()


def test_the_shipped_catalog_contains_no_ambiguous_name() -> None:
    """Live data may hold duplicates; what we author ourselves must not."""

    registry = default_alias_registry()

    for namespace in (_PRESET, ALIAS_NAMESPACE_WORKFLOW_RUNTIME, ALIAS_NAMESPACE_AGENT_ROLE):
        assert registry.ambiguous_names(namespace=namespace) == ()
    assert len(registry.entries(namespace=_PRESET)) == len(VISUAL_PROCESS_PRESET_ALIASES)


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("Code Review", "preset-code-review"),
        ("code review", "preset-code-review"),
        ("TDD", "preset-tdd-loop"),
        ("Testgetrieben", "preset-tdd-loop"),
        ("Gauntlet", "preset-builder-critic-gauntlet"),
        ("Builder-Critic", "preset-builder-critic-gauntlet"),
        ("RAG", "preset-rag-pipeline"),
        ("Codesuche", "preset-codecompass-search"),
        ("Auslieferung", "preset-deploy-pipeline"),
    ),
)
def test_a_person_finds_a_preset_by_any_of_its_names(text: str, expected: str) -> None:
    assert default_alias_registry().resolve(namespace=_PRESET, text=text) == expected


def test_the_technical_name_keeps_working_for_whoever_already_knows_it() -> None:
    """Renaming for newcomers must not break anyone's existing vocabulary."""

    registry = default_alias_registry()

    for canonical_id in VISUAL_PROCESS_PRESET_ALIASES:
        assert registry.resolve(namespace=_PRESET, text=canonical_id) == canonical_id


def test_roles_and_runtimes_resolve_in_their_own_namespaces() -> None:
    registry = default_alias_registry()

    assert registry.resolve(namespace=ALIAS_NAMESPACE_AGENT_ROLE, text="PO") == "product_owner"
    assert registry.resolve(namespace=ALIAS_NAMESPACE_WORKFLOW_RUNTIME, text="native") == "ananta-native"
    assert registry.resolve(namespace=ALIAS_NAMESPACE_WORKFLOW_RUNTIME, text="PO") is None


def test_every_role_the_canvas_offers_has_a_human_name() -> None:
    """The catalog and the canvas role list must not drift apart."""

    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / (
        "frontend-angular/src/app/features/caseflow/agent-canvas/caseflow-role-catalog.ts"
    )
    text = source.read_text(encoding="utf-8")
    for role_id in AGENT_ROLE_ALIASES:
        assert f"id: '{role_id}'" in text, role_id


class _Row:
    def __init__(self, identifier: str, name: str, description: str = "") -> None:
        self.id = identifier
        self.name = name
        self.description = description


class _Roles:
    def __init__(self, *rows: _Row) -> None:
        self._rows = rows

    def get_all(self) -> tuple[_Row, ...]:
        return self._rows


def test_a_persisted_role_is_reachable_by_its_uuid_and_by_its_name() -> None:
    """A UUID beside a human name is the plainest case the registry is for."""

    source = DatabaseRoleAliasSource(_Roles(_Row("b500b2a8-56e9", "Accessibility Specialist")))
    registry = AliasRegistry([source])

    assert (
        registry.display_name(
            namespace=ALIAS_NAMESPACE_AGENT_ROLE,
            canonical_id="b500b2a8-56e9",
        )
        == "Accessibility Specialist"
    )
    assert (
        registry.resolve(
            namespace=ALIAS_NAMESPACE_AGENT_ROLE,
            text="accessibility specialist",
        )
        == "b500b2a8-56e9"
    )


def test_an_unreachable_catalog_degrades_the_label_rather_than_the_screen() -> None:
    """A failed lookup must not take down every view that renders a name."""

    class _Broken:
        def get_all(self) -> tuple[_Row, ...]:
            raise RuntimeError("database unavailable")

    assert DatabaseRoleAliasSource(_Broken()).entries(ALIAS_NAMESPACE_AGENT_ROLE) == ()


def test_two_roles_sharing_a_name_stay_loadable_but_that_name_stops_resolving() -> None:
    """One duplicated row must not be able to take naming down everywhere."""

    source = DatabaseRoleAliasSource(_Roles(_Row("id-a", "Reviewer"), _Row("id-b", "Reviewer")))

    registry = AliasRegistry([source])

    assert registry.resolve(namespace=ALIAS_NAMESPACE_AGENT_ROLE, text="Reviewer") is None
    assert registry.ambiguous_names(namespace=ALIAS_NAMESPACE_AGENT_ROLE) == ("reviewer",)
    for identifier in ("id-a", "id-b"):
        assert registry.display_name(namespace=ALIAS_NAMESPACE_AGENT_ROLE, canonical_id=identifier) == "Reviewer"
    assert registry.resolve(namespace=ALIAS_NAMESPACE_AGENT_ROLE, text="id-a") == "id-a"


def test_the_database_source_ignores_namespaces_it_does_not_own() -> None:
    source = DatabaseRoleAliasSource(_Roles(_Row("id-a", "Reviewer")))

    assert source.entries(ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET) == ()
