"""Human-readable names for technical identifiers.

A great deal of what this system shows a person is an identifier that means
nothing to them: ``preset-builder-critic-gauntlet``, a runtime id, a role key,
a bare number.  Each one usually *has* an understandable name; that name just
lives in whichever component happened to render it, or nowhere at all.

This registry gives one place to say "this identifier is called that", in a
shape that stays useful as more kinds of identifier join:

* A **namespace** keeps two unrelated identifiers from colliding, so the same
  short alias can mean different things for presets and for roles.
* A **display name** is what a person should read.
* **Aliases** are the other names that must find the same thing — an older
  name, a shorter one, a German or English variant.

Two deliberate asymmetries. Rendering fails *open*: an unknown identifier is
shown as itself rather than hidden, because a raw id is worse than a name but
far better than a blank. Resolution fails *closed*: an unknown or ambiguous
alias resolves to nothing rather than to a guess, because silently acting on
the wrong entity is the failure this exists to prevent.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, final, runtime_checkable

ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET = "visual_process_preset"
ALIAS_NAMESPACE_AGENT_ROLE = "agent_role"
ALIAS_NAMESPACE_WORKFLOW_RUNTIME = "workflow_runtime"

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CANONICAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_MAX_ALIASES = 16
_MAX_DISPLAY_CHARS = 160


class AliasRegistryError(ValueError):
    """Stable fail-closed alias contract error."""


@final
@dataclass(frozen=True, slots=True)
class AliasEntry:
    """One identifier together with every name a person may use for it."""

    namespace: str
    canonical_id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or _NAMESPACE_RE.fullmatch(self.namespace) is None:
            raise AliasRegistryError("alias_namespace_invalid")
        if not isinstance(self.canonical_id, str) or _CANONICAL_RE.fullmatch(self.canonical_id) is None:
            raise AliasRegistryError("alias_canonical_id_invalid")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise AliasRegistryError("alias_display_name_invalid")
        if len(self.display_name) > _MAX_DISPLAY_CHARS:
            raise AliasRegistryError("alias_display_name_invalid")
        if not isinstance(self.aliases, tuple) or len(self.aliases) > _MAX_ALIASES:
            raise AliasRegistryError("alias_list_invalid")
        for value in self.aliases:
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_DISPLAY_CHARS:
                raise AliasRegistryError("alias_list_invalid")
        if not isinstance(self.description, str) or len(self.description) > 1_024:
            raise AliasRegistryError("alias_description_invalid")
        # Only the names a person chose have to be distinct. The canonical id
        # is an additional key that may legitimately coincide with them —
        # a runtime called "temporal" is displayed as "Temporal".
        chosen = [normalize_alias(value) for value in (self.display_name, *self.aliases)]
        if len(set(chosen)) != len(chosen):
            raise AliasRegistryError("alias_list_duplicate")

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Every name this entry answers to, display name included."""

        return (self.display_name, *self.aliases, self.canonical_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "aliases": list(self.aliases),
            "canonical_id": self.canonical_id,
            "description": self.description,
            "display_name": self.display_name,
            "namespace": self.namespace,
        }


@runtime_checkable
class AliasSource(Protocol):
    """A place aliases come from; several can be layered."""

    def entries(self, namespace: str) -> Sequence[AliasEntry]: ...


@final
class StaticAliasSource:
    """Aliases that ship with the code."""

    __slots__ = ("_by_namespace",)

    def __init__(self, entries: Iterable[AliasEntry]) -> None:
        grouped: dict[str, list[AliasEntry]] = {}
        for entry in entries:
            if not isinstance(entry, AliasEntry):
                raise AliasRegistryError("alias_entry_invalid")
            grouped.setdefault(entry.namespace, []).append(entry)
        self._by_namespace = {name: tuple(values) for name, values in grouped.items()}

    def entries(self, namespace: str) -> Sequence[AliasEntry]:
        return self._by_namespace.get(_namespace(namespace), ())


@final
class AliasRegistry:
    """Resolve identifiers to names and names back to identifiers.

    Later sources win on the same canonical id, so a deployment-specific
    source can rename what ships with the code without editing it.  An alias
    claimed by two different identifiers is rejected outright rather than
    resolved by order, because "whichever was registered first" is not an
    answer a person can predict.
    """

    __slots__ = ("_by_alias", "_by_id")

    def __init__(self, sources: Sequence[AliasSource], *, namespaces: Iterable[str] = ()) -> None:
        wanted = tuple(_namespace(value) for value in namespaces) or (
            ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
            ALIAS_NAMESPACE_AGENT_ROLE,
            ALIAS_NAMESPACE_WORKFLOW_RUNTIME,
        )
        by_id: dict[tuple[str, str], AliasEntry] = {}
        for source in sources:
            if not isinstance(source, AliasSource):
                raise AliasRegistryError("alias_source_invalid")
            for namespace in wanted:
                for entry in source.entries(namespace):
                    if entry.namespace != namespace:
                        raise AliasRegistryError("alias_namespace_mismatch")
                    by_id[(namespace, entry.canonical_id)] = entry
        by_alias: dict[tuple[str, str], str] = {}
        for (namespace, canonical_id), entry in by_id.items():
            for term in entry.search_terms:
                for variant in alias_keys(term):
                    key = (namespace, variant)
                    claimed = by_alias.get(key)
                    if claimed is not None and claimed != canonical_id:
                        raise AliasRegistryError("alias_ambiguous")
                    by_alias[key] = canonical_id
        self._by_id = by_id
        self._by_alias = by_alias

    def display_name(self, *, namespace: str, canonical_id: str) -> str:
        """Name a person should read; the identifier itself when unknown."""

        entry = self._by_id.get((_namespace(namespace), str(canonical_id)))
        return entry.display_name if entry is not None else str(canonical_id)

    def entry(self, *, namespace: str, canonical_id: str) -> AliasEntry | None:
        return self._by_id.get((_namespace(namespace), str(canonical_id)))

    def resolve(self, *, namespace: str, text: str) -> str | None:
        """Find the identifier a name refers to, or nothing at all."""

        if not isinstance(text, str) or not text.strip():
            return None
        wanted = _namespace(namespace)
        for variant in alias_keys(text):
            found = self._by_alias.get((wanted, variant))
            if found is not None:
                return found
        return None

    def entries(self, *, namespace: str) -> tuple[AliasEntry, ...]:
        wanted = _namespace(namespace)
        return tuple(
            entry for (entry_namespace, _canonical), entry in sorted(self._by_id.items()) if entry_namespace == wanted
        )

    def describe(self, *, namespace: str, canonical_id: str) -> dict[str, object]:
        """Render one identifier for a client that has to display it."""

        entry = self.entry(namespace=namespace, canonical_id=canonical_id)
        if entry is not None:
            return entry.to_dict()
        return {
            "aliases": [],
            "canonical_id": str(canonical_id),
            "description": "",
            "display_name": str(canonical_id),
            "namespace": _namespace(namespace),
        }


_TRANSLITERATIONS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def normalize_alias(value: str) -> str:
    """Fold a name to its primary lookup key.

    People type "Builder-Critic", "builder critic" and "Builder Critic" for
    the same thing, so all three have to land on one key.
    """

    return _fold(_transliterate(str(value)))


def alias_keys(value: str) -> tuple[str, ...]:
    """Every key a name should be findable under.

    German has two competing spellings for the same word — "Prüfen",
    "Pruefen" and, from anyone without an umlaut key, "Prufen". A person
    should not have to guess which one this system stored, so each name is
    indexed under all of them.
    """

    text = str(value)
    transliterated = _fold(_transliterate(text))
    stripped = _fold(text)
    return (transliterated,) if transliterated == stripped else (transliterated, stripped)


def _transliterate(value: str) -> str:
    for source, target in _TRANSLITERATIONS.items():
        value = value.replace(source, target).replace(source.upper(), target.upper())
    return value


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", stripped.casefold()).strip("-")


def alias_entries_from_mapping(
    namespace: str,
    raw: Mapping[str, Mapping[str, object]],
) -> tuple[AliasEntry, ...]:
    """Build entries from a plain mapping, for catalogs written as data."""

    wanted = _namespace(namespace)
    entries: list[AliasEntry] = []
    for canonical_id, value in raw.items():
        if not isinstance(value, Mapping):
            raise AliasRegistryError("alias_entry_invalid")
        aliases = value.get("aliases") or ()
        if isinstance(aliases, (str, bytes)) or not isinstance(aliases, Sequence):
            raise AliasRegistryError("alias_list_invalid")
        entries.append(
            AliasEntry(
                namespace=wanted,
                canonical_id=str(canonical_id),
                display_name=str(value.get("display_name") or ""),
                aliases=tuple(str(item) for item in aliases),
                description=str(value.get("description") or ""),
            )
        )
    return tuple(entries)


def _namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE_RE.fullmatch(value) is None:
        raise AliasRegistryError("alias_namespace_invalid")
    return value


__all__ = [
    "ALIAS_NAMESPACE_AGENT_ROLE",
    "ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET",
    "ALIAS_NAMESPACE_WORKFLOW_RUNTIME",
    "AliasEntry",
    "AliasRegistry",
    "AliasRegistryError",
    "AliasSource",
    "StaticAliasSource",
    "alias_entries_from_mapping",
    "alias_keys",
    "normalize_alias",
]
