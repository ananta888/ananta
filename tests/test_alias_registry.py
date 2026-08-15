"""Human-readable names for technical identifiers.

The two asymmetries this pins: rendering an unknown identifier falls back to
the identifier, because a raw id beats a blank; resolving an unknown or
ambiguous name yields nothing, because acting on the wrong entity is the
failure the registry exists to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.alias_registry import (
    ALIAS_NAMESPACE_AGENT_ROLE,
    ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
    AliasEntry,
    AliasRegistry,
    AliasRegistryError,
    StaticAliasSource,
    alias_entries_from_mapping,
    alias_keys,
    normalize_alias,
)

_PRESET = ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET
_ROLE = ALIAS_NAMESPACE_AGENT_ROLE


def _entry(**overrides: Any) -> AliasEntry:
    values: dict[str, Any] = {
        "namespace": _PRESET,
        "canonical_id": "preset-builder-critic-gauntlet",
        "display_name": "Bauen & Prüfen",
        "aliases": ("Builder-Critic", "Gauntlet"),
        "description": "Ein Lead verteilt, ein Builder baut, ein Critic prüft.",
    }
    values.update(overrides)
    return AliasEntry(**values)


def _registry(*entries: AliasEntry) -> AliasRegistry:
    return AliasRegistry([StaticAliasSource(entries or (_entry(),))])


def test_an_identifier_renders_as_its_human_name() -> None:
    registry = _registry()

    assert registry.display_name(namespace=_PRESET, canonical_id="preset-builder-critic-gauntlet") == "Bauen & Prüfen"


def test_an_unknown_identifier_renders_as_itself_rather_than_blank() -> None:
    registry = _registry()

    assert registry.display_name(namespace=_PRESET, canonical_id="preset-unknown") == "preset-unknown"


@pytest.mark.parametrize(
    "text",
    (
        "Bauen & Prüfen",
        "bauen & prüfen",
        "BAUEN & PRUEFEN",
        "bauen-prufen",
        "Builder-Critic",
        "builder critic",
        "GAUNTLET",
        "preset-builder-critic-gauntlet",
    ),
)
def test_every_reasonable_spelling_finds_the_same_thing(text: str) -> None:
    registry = _registry()

    assert registry.resolve(namespace=_PRESET, text=text) == "preset-builder-critic-gauntlet"


@pytest.mark.parametrize("text", ("", "   ", "quatsch", "Bauen und Prüfen"))
def test_a_name_nobody_registered_resolves_to_nothing(text: str) -> None:
    """Including a plausible-but-different wording: "und" is not "&"."""

    registry = _registry()

    assert registry.resolve(namespace=_PRESET, text=text) is None


def test_a_namespace_keeps_identical_aliases_apart() -> None:
    """The same short word may mean different things in different contexts."""

    registry = AliasRegistry(
        [
            StaticAliasSource(
                (
                    _entry(canonical_id="preset-a", display_name="Prüfen", aliases=()),
                    AliasEntry(namespace=_ROLE, canonical_id="reviewer", display_name="Prüfen"),
                )
            )
        ]
    )

    assert registry.resolve(namespace=_PRESET, text="Prüfen") == "preset-a"
    assert registry.resolve(namespace=_ROLE, text="Prüfen") == "reviewer"


def test_a_name_two_identifiers_claim_stops_resolving_rather_than_guessing() -> None:
    """ "Whichever was registered first" is not an answer a person can predict."""

    registry = AliasRegistry(
        [
            StaticAliasSource(
                (
                    _entry(canonical_id="preset-a", display_name="Bauen", aliases=("Duplikat",)),
                    _entry(canonical_id="preset-b", display_name="Prüfen", aliases=("Duplikat",)),
                )
            )
        ]
    )

    assert registry.resolve(namespace=_PRESET, text="Duplikat") is None
    assert registry.ambiguous_names(namespace=_PRESET) == ("duplikat",)
    # The names nobody contests keep working.
    assert registry.resolve(namespace=_PRESET, text="Bauen") == "preset-a"
    assert registry.resolve(namespace=_PRESET, text="Prüfen") == "preset-b"


def test_a_later_source_renames_what_ships_with_the_code() -> None:
    shipped = StaticAliasSource((_entry(display_name="Bauen & Prüfen"),))
    deployment = StaticAliasSource((_entry(display_name="Unser Review-Team", aliases=()),))

    registry = AliasRegistry([shipped, deployment])

    assert (
        registry.display_name(
            namespace=_PRESET,
            canonical_id="preset-builder-critic-gauntlet",
        )
        == "Unser Review-Team"
    )


def test_an_entry_repeating_a_name_is_rejected() -> None:
    with pytest.raises(AliasRegistryError, match="alias_list_duplicate"):
        _entry(aliases=("Gauntlet", "gauntlet"))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("namespace", "Nicht Erlaubt"),
        ("canonical_id", ""),
        ("display_name", "  "),
        ("aliases", ("",)),
        ("aliases", tuple(f"a{index}" for index in range(17))),
    ),
)
def test_a_malformed_entry_fails_closed(field: str, value: Any) -> None:
    with pytest.raises(AliasRegistryError):
        _entry(**{field: value})


def test_describe_renders_an_unknown_identifier_without_inventing_a_name() -> None:
    registry = _registry()

    described = registry.describe(namespace=_PRESET, canonical_id="preset-unknown")

    assert described["display_name"] == "preset-unknown"
    assert described["aliases"] == []
    assert described["description"] == ""


def test_entries_are_listed_in_a_stable_order() -> None:
    registry = _registry(
        _entry(canonical_id="preset-b", display_name="Zweitens", aliases=()),
        _entry(canonical_id="preset-a", display_name="Erstens", aliases=()),
    )

    listed = [entry.canonical_id for entry in registry.entries(namespace=_PRESET)]

    assert listed == ["preset-a", "preset-b"]


def test_german_spellings_share_a_key_but_different_words_do_not() -> None:
    assert normalize_alias("Prüfen") == normalize_alias("Pruefen")
    assert "prufen" in alias_keys("Prüfen")
    assert normalize_alias("Bauen & Prüfen") != normalize_alias("Bauen und Prüfen")


def test_a_catalog_written_as_data_becomes_entries() -> None:
    entries = alias_entries_from_mapping(
        _PRESET,
        {"preset-a": {"display_name": "Erstens", "aliases": ["Eins"], "description": "Beschreibung"}},
    )

    assert len(entries) == 1
    assert entries[0].display_name == "Erstens"
    assert entries[0].aliases == ("Eins",)
