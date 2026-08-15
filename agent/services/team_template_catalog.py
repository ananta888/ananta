"""One selectable list of every team and process template that exists.

Templates live in three places today and answer two different questions.
Team types in the database say *who works together* — Scrum, TDD, Code-Repair,
each with its allowed roles. Visual-process presets say *how the work flows* —
a code review pipeline, a RAG pipeline. CodeHug additionally ships a hardcoded
list of team names that turned out to be a stale mirror of the database.

Rather than pick a winner, this catalog offers all of them under one contract,
labelled by which question they answer. Nothing is hidden and nothing is
duplicated: the hardcoded mirror is dropped in favour of the table it copied,
and everything carries the readable names the alias registry resolves.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.alias_registry import (
    ALIAS_NAMESPACE_AGENT_ROLE,
    ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
    AliasRegistry,
)

TEAM_TEMPLATE_KIND_TEAM = "team"
TEAM_TEMPLATE_KIND_PROCESS = "process"
TEAM_TEMPLATE_KINDS = frozenset({TEAM_TEMPLATE_KIND_TEAM, TEAM_TEMPLATE_KIND_PROCESS})

TEAM_TEMPLATE_CATALOG_SCHEMA = "ananta.team_template_catalog.v1"

_MAX_ROLES = 64


class TeamTemplateCatalogError(ValueError):
    """Stable fail-closed catalog contract error."""


@final
@dataclass(frozen=True, slots=True)
class TeamTemplateRole:
    """One role a template asks for, by identity and by name."""

    role_id: str
    display_name: str

    def to_dict(self) -> dict[str, str]:
        return {"display_name": self.display_name, "role_id": self.role_id}


@final
@dataclass(frozen=True, slots=True)
class TeamTemplate:
    """One template a person can pick, whichever source it came from."""

    template_id: str
    kind: str
    source_id: str
    display_name: str
    description: str
    roles: tuple[TeamTemplateRole, ...]
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in TEAM_TEMPLATE_KINDS:
            raise TeamTemplateCatalogError("team_template_kind_invalid")
        for name in ("template_id", "source_id", "display_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TeamTemplateCatalogError(f"team_template_{name}_invalid")
        if len(self.roles) > _MAX_ROLES:
            raise TeamTemplateCatalogError("team_template_roles_invalid")

    @property
    def agent_count(self) -> int:
        """How many agents this template starts you with."""

        return len(self.roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_count": self.agent_count,
            "aliases": list(self.aliases),
            "description": self.description,
            "display_name": self.display_name,
            "kind": self.kind,
            "roles": [role.to_dict() for role in self.roles],
            "source_id": self.source_id,
            "template_id": self.template_id,
        }


@runtime_checkable
class TeamTypeReadPort(Protocol):
    """The team types and their allowed roles, as the database holds them."""

    def team_types(self) -> Sequence[Any]: ...

    def role_ids_for(self, team_type_id: str) -> Sequence[str]: ...

    def roles(self) -> Sequence[Any]: ...


@final
class RepositoryTeamTypeReadPort:
    """Adapts the existing team repositories to the read port."""

    __slots__ = ("_links", "_roles", "_types")

    def __init__(self, *, team_types: Any, role_links: Any, roles: Any) -> None:
        self._types = team_types
        self._links = role_links
        self._roles = roles

    def team_types(self) -> Sequence[Any]:
        return list(self._types.get_all())

    def role_ids_for(self, team_type_id: str) -> Sequence[str]:
        return list(self._links.get_allowed_role_ids(team_type_id))

    def roles(self) -> Sequence[Any]:
        return list(self._roles.get_all())


@final
class TeamTemplateCatalog:
    """Every template, from every source, in one list."""

    __slots__ = ("_aliases", "_presets", "_team_types")

    def __init__(
        self,
        *,
        team_types: TeamTypeReadPort | None,
        presets: Sequence[dict[str, Any]],
        aliases: AliasRegistry,
    ) -> None:
        if team_types is not None and not isinstance(team_types, TeamTypeReadPort):
            raise TeamTemplateCatalogError("team_template_read_port_invalid")
        if not isinstance(aliases, AliasRegistry):
            raise TeamTemplateCatalogError("team_template_aliases_invalid")
        self._team_types = team_types
        self._presets = list(presets)
        self._aliases = aliases

    def list(self) -> tuple[TeamTemplate, ...]:
        """Team templates first, then process templates, each name-sorted.

        Teams come first because picking who works together is the decision a
        person makes first; the flow can be chosen or changed afterwards.
        """

        return (*self._team_templates(), *self._process_templates())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TEAM_TEMPLATE_CATALOG_SCHEMA,
            "templates": [template.to_dict() for template in self.list()],
        }

    def _team_templates(self) -> tuple[TeamTemplate, ...]:
        if self._team_types is None:
            return ()
        try:
            types = self._team_types.team_types()
            role_rows = self._team_types.roles()
        except Exception:
            # A catalog that cannot read one source still offers the others.
            return ()
        names = {str(getattr(row, "id", "")): str(getattr(row, "name", "") or "") for row in role_rows}
        templates: list[TeamTemplate] = []
        for row in types:
            source_id = str(getattr(row, "id", "") or "")
            name = str(getattr(row, "name", "") or "").strip()
            if not source_id or not name:
                continue
            try:
                role_ids = self._team_types.role_ids_for(source_id)
            except Exception:
                role_ids = ()
            roles = tuple(
                TeamTemplateRole(
                    role_id=str(role_id),
                    display_name=names.get(str(role_id))
                    or self._aliases.display_name(
                        namespace=ALIAS_NAMESPACE_AGENT_ROLE,
                        canonical_id=str(role_id),
                    ),
                )
                for role_id in role_ids[:_MAX_ROLES]
            )
            templates.append(
                TeamTemplate(
                    template_id=f"{TEAM_TEMPLATE_KIND_TEAM}:{source_id}",
                    kind=TEAM_TEMPLATE_KIND_TEAM,
                    source_id=source_id,
                    display_name=name,
                    description=str(getattr(row, "description", "") or ""),
                    roles=roles,
                    aliases=(),
                )
            )
        return tuple(sorted(templates, key=lambda item: item.display_name.casefold()))

    def _process_templates(self) -> tuple[TeamTemplate, ...]:
        templates: list[TeamTemplate] = []
        for preset in self._presets:
            source_id = str(preset.get("id") or "")
            if not source_id:
                continue
            described = self._aliases.describe(
                namespace=ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
                canonical_id=source_id,
            )
            templates.append(
                TeamTemplate(
                    template_id=f"{TEAM_TEMPLATE_KIND_PROCESS}:{source_id}",
                    kind=TEAM_TEMPLATE_KIND_PROCESS,
                    source_id=source_id,
                    display_name=str(described.get("display_name") or source_id),
                    description=str(described.get("description") or preset.get("description") or ""),
                    roles=(),
                    aliases=tuple(str(value) for value in (described.get("aliases") or ())),
                )
            )
        return tuple(sorted(templates, key=lambda item: item.display_name.casefold()))


__all__ = [
    "TEAM_TEMPLATE_CATALOG_SCHEMA",
    "TEAM_TEMPLATE_KINDS",
    "TEAM_TEMPLATE_KIND_PROCESS",
    "TEAM_TEMPLATE_KIND_TEAM",
    "RepositoryTeamTypeReadPort",
    "TeamTemplate",
    "TeamTemplateCatalog",
    "TeamTemplateCatalogError",
    "TeamTemplateRole",
    "TeamTypeReadPort",
]
