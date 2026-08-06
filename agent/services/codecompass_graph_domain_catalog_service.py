"""Project-bound domain hierarchy and scope selection for CodeCompass graphs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

# Domain values originate in graph artifacts and therefore must be treated as
# untrusted input.  Besides bounding each individual value, the expanded budget
# limits the cumulative prefix strings retained for a hierarchy.  This prevents
# a deeply nested value from turning a linear input into an unbounded quadratic
# allocation while leaving ordinary package and repository paths unchanged.
_MAX_DOMAIN_TEXT_CHARACTERS = 4_096
_MAX_DOMAIN_SEGMENT_CHARACTERS = 255
_MAX_DOMAIN_HIERARCHY_DEPTH = 64
_MAX_DOMAIN_EXPANDED_CHARACTERS = 65_536


def _node_id(node: Mapping[str, object]) -> str:
    return str(node.get("id") or node.get("node_id") or "").strip()


def _nested_records(node: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    records: list[Mapping[str, object]] = [node]
    for field in ("attributes", "attrs", "source_record"):
        candidate = node.get(field)
        if isinstance(candidate, Mapping):
            records.append(candidate)
    return tuple(records)


def _first_text(
    records: Sequence[Mapping[str, object]],
    *fields: str,
    maximum_characters: int,
) -> tuple[str, bool]:
    for field in fields:
        for record in records:
            value = record.get(field)
            if value is None:
                continue
            text = value if isinstance(value, str) else str(value)
            if len(text) > maximum_characters:
                return "", True
            text = text.strip()
            if text:
                return text, False
    return "", False


def _first_provenance_file(
    records: Sequence[Mapping[str, object]],
) -> tuple[str, bool]:
    for record in records:
        provenance = record.get("provenance")
        if not isinstance(provenance, Mapping):
            continue
        value = provenance.get("file") or provenance.get("path")
        text = value if isinstance(value, str) else str(value or "")
        if len(text) > _MAX_DOMAIN_TEXT_CHARACTERS:
            return "", True
        text = text.strip()
        if text:
            return text, False
    return "", False


def _bounded_parts(
    value: str,
    *,
    separator: str,
    maximum_parts: int,
) -> tuple[str, ...] | None:
    """Split a hierarchy value without materializing an unbounded segment list."""

    # ``maxsplit`` ensures an attacker-controlled separator count cannot create
    # a large temporary list before the depth check runs.
    raw_parts = value.split(separator, maximum_parts)
    if len(raw_parts) > maximum_parts:
        return None
    parts: list[str] = []
    for part in raw_parts:
        if not part or part in {".", ".."}:
            continue
        if len(part) > _MAX_DOMAIN_SEGMENT_CHARACTERS:
            return None
        parts.append(part)
    if len(parts) > maximum_parts:
        return None
    return tuple(parts)


def _scope_key(source: str, value: str) -> str:
    digest = hashlib.sha256(f"codecompass-domain-scope.v1\0{source}\0{value}".encode("utf-8")).hexdigest()
    return f"{source}:{digest}"


@dataclass(frozen=True)
class _DomainIdentity:
    source: str
    value: str
    label: str
    parent_value: str | None
    depth: int

    @property
    def key(self) -> str:
        return _scope_key(self.source, self.value)

    @property
    def parent_key(self) -> str | None:
        if self.parent_value is None:
            return None
        return _scope_key(self.source, self.parent_value)


@dataclass(frozen=True)
class CodeCompassGraphDomainFacet:
    key: str
    label: str
    parent_key: str | None
    depth: int
    direct_node_count: int
    subtree_node_count: int
    has_children: bool
    source: str
    path: str

    def to_wire(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "parent_key": self.parent_key,
            "depth": self.depth,
            "direct_node_count": self.direct_node_count,
            "subtree_node_count": self.subtree_node_count,
            "has_children": self.has_children,
            "source": self.source,
            "path": self.path,
        }


@dataclass(frozen=True)
class CodeCompassGraphDomainCatalog:
    facets: tuple[CodeCompassGraphDomainFacet, ...]
    total_node_count: int
    assigned_node_count: int
    unassigned_node_count: int


@dataclass(frozen=True)
class CodeCompassGraphDomainSelection:
    nodes: tuple[Mapping[str, object], ...]
    facet: CodeCompassGraphDomainFacet | None
    global_node_count: int


@dataclass(frozen=True)
class _DomainNodeBinding:
    node: Mapping[str, object]
    scope_keys: tuple[str, ...]


@dataclass(frozen=True)
class CodeCompassGraphDomainIndex:
    """Prepared domain facts reusable across inventory and scope reads."""

    domain_catalog: CodeCompassGraphDomainCatalog
    _bindings: tuple[_DomainNodeBinding, ...]

    def select(
        self,
        *,
        scope_key: str | None,
        include_descendants: bool,
    ) -> CodeCompassGraphDomainSelection:
        normalized_scope = str(scope_key or "").strip()
        if not normalized_scope:
            return CodeCompassGraphDomainSelection(
                nodes=tuple(binding.node for binding in self._bindings),
                facet=None,
                global_node_count=self.domain_catalog.total_node_count,
            )
        facet = next(
            (candidate for candidate in self.domain_catalog.facets if candidate.key == normalized_scope),
            None,
        )
        if facet is None:
            raise ValueError("graph_domain_scope_unknown")
        selected = tuple(
            binding.node
            for binding in self._bindings
            if (
                normalized_scope in binding.scope_keys
                if include_descendants
                else binding.scope_keys[-1] == normalized_scope
            )
        )
        return CodeCompassGraphDomainSelection(
            nodes=selected,
            facet=facet,
            global_node_count=self.domain_catalog.total_node_count,
        )


class CodeCompassGraphDomainCatalogPort(Protocol):
    def prepare(
        self,
        *,
        nodes: Sequence[object],
    ) -> CodeCompassGraphDomainIndex: ...

    def catalog(
        self,
        *,
        nodes: Sequence[object],
    ) -> CodeCompassGraphDomainCatalog: ...

    def select(
        self,
        *,
        nodes: Sequence[object],
        scope_key: str | None,
        include_descendants: bool,
    ) -> CodeCompassGraphDomainSelection: ...


class CodeCompassGraphDomainCatalogService:
    """Derive a deterministic hierarchy without changing stored graph facts."""

    _SOURCE_ORDER = {"domain_id": 0, "domain_path": 1, "path": 2, "unassigned": 3}

    def catalog(
        self,
        *,
        nodes: Sequence[object],
    ) -> CodeCompassGraphDomainCatalog:
        return self.prepare(nodes=nodes).domain_catalog

    def prepare(
        self,
        *,
        nodes: Sequence[object],
    ) -> CodeCompassGraphDomainIndex:
        canonical = self._canonical_nodes(nodes)
        accumulators: dict[str, dict[str, object]] = {}
        bindings: list[_DomainNodeBinding] = []
        assigned = 0
        for node in canonical:
            chain = self._domain_chain(node)
            bindings.append(
                _DomainNodeBinding(
                    node=node,
                    scope_keys=tuple(identity.key for identity in chain),
                )
            )
            if chain[0].source != "unassigned":
                assigned += 1
            for identity in chain:
                accumulator = accumulators.setdefault(
                    identity.key,
                    {
                        "identity": identity,
                        "direct": 0,
                        "subtree": 0,
                        "children": set(),
                    },
                )
                accumulator["subtree"] = int(accumulator["subtree"]) + 1
            leaf = chain[-1]
            accumulators[leaf.key]["direct"] = int(accumulators[leaf.key]["direct"]) + 1
            for parent, child in zip(chain, chain[1:]):
                children = accumulators[parent.key]["children"]
                if isinstance(children, set):
                    children.add(child.key)

        facets = tuple(
            self._facet(accumulator)
            for accumulator in sorted(
                accumulators.values(),
                key=self._sort_key,
            )
        )
        return CodeCompassGraphDomainIndex(
            domain_catalog=CodeCompassGraphDomainCatalog(
                facets=facets,
                total_node_count=len(canonical),
                assigned_node_count=assigned,
                unassigned_node_count=len(canonical) - assigned,
            ),
            _bindings=tuple(bindings),
        )

    def select(
        self,
        *,
        nodes: Sequence[object],
        scope_key: str | None,
        include_descendants: bool,
    ) -> CodeCompassGraphDomainSelection:
        return self.prepare(nodes=nodes).select(
            scope_key=scope_key,
            include_descendants=include_descendants,
        )

    @staticmethod
    def _canonical_nodes(
        nodes: Sequence[object],
    ) -> tuple[Mapping[str, object], ...]:
        result: list[Mapping[str, object]] = []
        seen: set[str] = set()
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            identifier = _node_id(node)
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            result.append(node)
        return tuple(result)

    def _domain_chain(
        self,
        node: Mapping[str, object],
    ) -> tuple[_DomainIdentity, ...]:
        records = _nested_records(node)
        explicit_id, invalid = _first_text(
            records,
            "domain_id",
            maximum_characters=_MAX_DOMAIN_TEXT_CHARACTERS,
        )
        if invalid:
            return self._unassigned_chain()
        if explicit_id:
            return self._explicit_chain(explicit_id, "domain_id")
        domain_path, invalid = _first_text(
            records,
            "domain_path",
            maximum_characters=_MAX_DOMAIN_TEXT_CHARACTERS,
        )
        if invalid:
            return self._unassigned_chain()
        if domain_path:
            return self._explicit_chain(domain_path, "domain_path")
        file_path, invalid = _first_text(
            records,
            "file",
            "path",
            maximum_characters=_MAX_DOMAIN_TEXT_CHARACTERS,
        )
        if invalid:
            return self._unassigned_chain()
        if not file_path:
            file_path, invalid = _first_provenance_file(records)
            if invalid:
                return self._unassigned_chain()
        if file_path:
            return self._path_chain(node, file_path)
        return self._unassigned_chain()

    @classmethod
    def _explicit_chain(
        cls,
        raw_value: str,
        source: str,
    ) -> tuple[_DomainIdentity, ...]:
        if len(raw_value) > _MAX_DOMAIN_TEXT_CHARACTERS:
            return cls._unassigned_chain()
        normalized = raw_value.replace("\\", "/").strip().strip("/")
        if normalized.startswith("ts:"):
            prefix = "ts:"
            separator = "/"
            candidate = normalized[3:].strip("/")
        elif "/" in normalized:
            prefix = ""
            separator = "/"
            candidate = normalized
        else:
            prefix = ""
            separator = "."
            candidate = normalized
        parts = _bounded_parts(
            candidate,
            separator=separator,
            maximum_parts=_MAX_DOMAIN_HIERARCHY_DEPTH,
        )
        if parts is None:
            return cls._unassigned_chain()
        if not parts:
            fallback_value = normalized or raw_value.strip()
            if not fallback_value or len(fallback_value) > _MAX_DOMAIN_SEGMENT_CHARACTERS:
                return cls._unassigned_chain()
            return (
                _DomainIdentity(
                    source=source,
                    value=fallback_value,
                    label=fallback_value,
                    parent_value=None,
                    depth=0,
                ),
            )
        chain = cls._identity_chain(
            parts=parts,
            source=source,
            separator=separator,
            prefix=prefix,
        )
        return chain or cls._unassigned_chain()

    @classmethod
    def _path_chain(
        cls,
        node: Mapping[str, object],
        raw_path: str,
    ) -> tuple[_DomainIdentity, ...]:
        if len(raw_path) > _MAX_DOMAIN_TEXT_CHARACTERS:
            return cls._unassigned_chain()
        normalized_path = raw_path.replace("\\", "/").strip()
        if normalized_path.startswith("/") or (len(normalized_path) > 1 and normalized_path[1] == ":"):
            return cls._unassigned_chain()
        records = _nested_records(node)
        raw_kind, invalid = _first_text(
            records,
            "raw_node_type",
            "node_type",
            "kind",
            "type",
            maximum_characters=_MAX_DOMAIN_SEGMENT_CHARACTERS,
        )
        if invalid:
            return cls._unassigned_chain()
        kind = raw_kind.lower()
        path_ends_in_directory = normalized_path.endswith("/")
        maximum_parts = _MAX_DOMAIN_HIERARCHY_DEPTH
        if not path_ends_in_directory and kind not in {"directory", "repository"}:
            maximum_parts += 1
        parts = _bounded_parts(
            normalized_path.strip("/"),
            separator="/",
            maximum_parts=maximum_parts,
        )
        if parts is None:
            return cls._unassigned_chain()
        if parts and not path_ends_in_directory and kind not in {"directory", "repository"}:
            parts = parts[:-1]
        if not parts or len(parts) > _MAX_DOMAIN_HIERARCHY_DEPTH:
            return cls._unassigned_chain()
        chain = cls._identity_chain(
            parts=parts,
            source="path",
            separator="/",
            prefix="",
        )
        return chain or cls._unassigned_chain()

    @staticmethod
    def _identity_chain(
        *,
        parts: Sequence[str],
        source: str,
        separator: str,
        prefix: str,
    ) -> tuple[_DomainIdentity, ...] | None:
        chain: list[_DomainIdentity] = []
        current = prefix
        parent_value: str | None = None
        expanded_characters = 0
        for depth, part in enumerate(parts):
            if current and current != prefix:
                current = f"{current}{separator}{part}"
            elif prefix:
                current = f"{prefix}{part}"
            else:
                current = part
            expanded_characters += len(current)
            if expanded_characters > _MAX_DOMAIN_EXPANDED_CHARACTERS:
                return None
            chain.append(
                _DomainIdentity(
                    source=source,
                    value=current,
                    label=part,
                    parent_value=parent_value,
                    depth=depth,
                )
            )
            parent_value = current
        return tuple(chain)

    @staticmethod
    def _unassigned_chain() -> tuple[_DomainIdentity, ...]:
        return (
            _DomainIdentity(
                source="unassigned",
                value="unassigned",
                label="Nicht zugeordnet",
                parent_value=None,
                depth=0,
            ),
        )

    def _sort_key(self, accumulator: Mapping[str, object]) -> tuple[object, ...]:
        identity = accumulator["identity"]
        if not isinstance(identity, _DomainIdentity):
            return (99, "")
        separator = "/" if "/" in identity.value or identity.value.startswith("ts:") else "."
        return (
            self._SOURCE_ORDER.get(identity.source, 99),
            tuple(part.casefold() for part in identity.value.split(separator)),
            identity.value,
        )

    @staticmethod
    def _facet(accumulator: Mapping[str, object]) -> CodeCompassGraphDomainFacet:
        identity = accumulator["identity"]
        if not isinstance(identity, _DomainIdentity):
            raise ValueError("graph_domain_catalog_internal_invalid")
        children = accumulator["children"]
        return CodeCompassGraphDomainFacet(
            key=identity.key,
            label=identity.label,
            parent_key=identity.parent_key,
            depth=identity.depth,
            direct_node_count=int(accumulator["direct"]),
            subtree_node_count=int(accumulator["subtree"]),
            has_children=bool(children),
            source=identity.source,
            path=identity.value,
        )


codecompass_graph_domain_catalog_service = CodeCompassGraphDomainCatalogService()


def get_codecompass_graph_domain_catalog_service() -> CodeCompassGraphDomainCatalogService:
    return codecompass_graph_domain_catalog_service


__all__ = [
    "CodeCompassGraphDomainCatalog",
    "CodeCompassGraphDomainCatalogPort",
    "CodeCompassGraphDomainCatalogService",
    "CodeCompassGraphDomainFacet",
    "CodeCompassGraphDomainIndex",
    "CodeCompassGraphDomainSelection",
    "get_codecompass_graph_domain_catalog_service",
]
