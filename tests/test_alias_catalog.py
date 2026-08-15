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
    default_alias_registry,
)
from agent.services.alias_registry import (
    ALIAS_NAMESPACE_AGENT_ROLE,
    ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
    ALIAS_NAMESPACE_WORKFLOW_RUNTIME,
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


def test_the_whole_catalog_loads_without_an_ambiguous_name() -> None:
    """Construction is where ambiguity is caught, so loading it is the test."""

    registry = default_alias_registry()

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
