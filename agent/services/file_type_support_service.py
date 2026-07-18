"""Read-only Hub projection of the CodeCompass file-type support registry."""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from ananta_contracts.file_type_support import (
    CapabilityDimension,
    FileTypeDescriptor,
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)

_SUPPORT_LEVELS = frozenset(
    {
        "discovery",
        "text_index",
        "symbol_index",
        "semantic_graph",
        "domain_parser",
        "unsupported",
    }
)


class FileTypeSupportFilterError(ValueError):
    """Raised when a support-matrix filter cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class FileTypeSupportFilter:
    """Validated, deterministic filter for the flat support-matrix rows."""

    priorities: tuple[str, ...] = ()
    support_levels: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    pipelines: tuple[str, ...] = ()
    missing_parser: bool | None = None
    missing_runtime: bool | None = None
    enabled: bool | None = None

    @classmethod
    def build(
        cls,
        *,
        priorities: Iterable[str] = (),
        support_levels: Iterable[str] = (),
        dimensions: Iterable[str] = (),
        pipelines: Iterable[str] = (),
        missing_parser: bool | None = None,
        missing_runtime: bool | None = None,
        enabled: bool | None = None,
    ) -> "FileTypeSupportFilter":
        return cls(
            priorities=_normalized_values(priorities, transform=str.upper),
            support_levels=_normalized_values(support_levels, transform=str.lower),
            dimensions=_normalized_values(dimensions, transform=str.lower),
            pipelines=_normalized_values(pipelines, transform=str.lower),
            missing_parser=missing_parser,
            missing_runtime=missing_runtime,
            enabled=enabled,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "priority": list(self.priorities),
            "support_level": list(self.support_levels),
            "dimension": list(self.dimensions),
            "pipeline": list(self.pipelines),
            "missing_parser": self.missing_parser,
            "missing_runtime": self.missing_runtime,
            "enabled": self.enabled,
        }


class FileTypeSupportService:
    """Expose registry truth without granting indexing or execution rights.

    Registry loading, runtime probing, filtering, and HTTP serialization stay
    outside parser implementations.  This keeps the Hub's read-only policy
    projection independently testable and prevents the registry from becoming
    an authorization list.
    """

    def __init__(
        self,
        repository_root: Path,
        *,
        registry: FileTypeSupportRegistry | None = None,
        runtime_availability: Mapping[str, bool] | None = None,
        runtime_scope: str = "current_process",
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.registry = registry or load_file_type_support_registry(self.repository_root)
        self.runtime_availability = (
            dict(runtime_availability)
            if runtime_availability is not None
            else probe_file_type_runtime_requirements(self.registry)
        )
        self.runtime_scope = str(runtime_scope or "current_process")

    def support_matrix(
        self,
        filters: FileTypeSupportFilter | None = None,
    ) -> dict[str, Any]:
        query = filters or FileTypeSupportFilter()
        self._validate_filter(query)
        raw_matrix = self.registry.support_matrix(
            runtime_availability=self.runtime_availability,
        )
        rows: list[dict[str, Any]] = []
        for raw_row in raw_matrix["rows"]:
            descriptor = self.registry.descriptor(str(raw_row["format_id"]))
            if descriptor is None:  # fail closed for malformed programmatic registries
                continue
            row = self._project_row(descriptor, raw_row)
            if self._matches(row, query):
                rows.append(row)
        rows.sort(key=lambda item: (str(item["format_id"]), str(item["pipeline"])))
        return {
            **{key: value for key, value in raw_matrix.items() if key != "rows"},
            "filters": query.as_dict(),
            "runtime_availability": dict(sorted(self.runtime_availability.items())),
            "runtime_scope": self.runtime_scope,
            "runtime_notice": "worker_pipeline_runtime_must_be_reported_by_the_executing_worker",
            "authorization_notice": "registry_support_does_not_grant_file_access_or_execution",
            "row_count": len(rows),
            "rows": rows,
        }

    def _project_row(
        self,
        descriptor: FileTypeDescriptor,
        raw_row: Mapping[str, Any],
    ) -> dict[str, Any]:
        capabilities = dict(raw_row["capabilities"])
        support_level = _support_level(
            descriptor,
            capabilities,
            pipeline=str(raw_row["pipeline"]),
        )
        parser_dimensions = [
            dimension.value
            for dimension in CapabilityDimension
            if capabilities[dimension.value]["configured"]
            and capabilities[dimension.value]["implementation"] == "parser"
        ]
        parser_producers = sorted(
            {
                str(capabilities[dimension]["producer"])
                for dimension in parser_dimensions
                if capabilities[dimension].get("producer")
            }
        )
        missing_runtime_dimensions = [
            dimension.value
            for dimension in CapabilityDimension
            if capabilities[dimension.value]["runtime_requirements"]
            and capabilities[dimension.value]["runtime_available"] is not True
        ]
        semantic_producers = sorted(
            {
                declaration.producer
                for dimension in CapabilityDimension
                for declaration in [
                    descriptor.support_for("semantic_translation").capability(dimension)
                ]
                if declaration.configured and declaration.producer
            }
        )
        selectors = descriptor.selectors
        return {
            **dict(raw_row),
            "support_level": support_level,
            "selectors": {
                "extensions": sorted(selectors.extensions),
                "exact_filenames": sorted(selectors.exact_filenames),
                "patterns": sorted(selectors.filename_patterns),
                "compound_suffixes": sorted(selectors.compound_suffixes),
                "shebangs": sorted(selectors.shebang_patterns),
                "text_fallback": selectors.text_fallback,
            },
            "parser_strategy": descriptor.parser_strategy,
            "parser": {
                "configured": bool(parser_dimensions),
                "effective": any(
                    capabilities[dimension]["effective"]
                    for dimension in parser_dimensions
                ),
                "dimensions": parser_dimensions,
                "producers": parser_producers,
            },
            "fallback_strategy": descriptor.fallback_strategy,
            "fallback": {"strategy": descriptor.fallback_strategy},
            "semantic_adapter": semantic_producers[0] if semantic_producers else None,
            "semantic_adapters": semantic_producers,
            "known_limits": list(descriptor.known_limits),
            "security": descriptor.security_class,
            "security_class": descriptor.security_class,
            "missing_parser": not parser_dimensions,
            "missing_runtime": bool(missing_runtime_dimensions),
            "missing_runtime_dimensions": missing_runtime_dimensions,
        }

    def _validate_filter(self, query: FileTypeSupportFilter) -> None:
        known_priorities = {descriptor.priority.upper() for descriptor in self.registry.descriptors}
        _reject_unknown("priority", query.priorities, known_priorities)
        _reject_unknown("support_level", query.support_levels, _SUPPORT_LEVELS)
        _reject_unknown(
            "dimension",
            query.dimensions,
            {dimension.value for dimension in CapabilityDimension},
        )
        _reject_unknown("pipeline", query.pipelines, set(self.registry.pipelines))
        for field_name in ("missing_parser", "missing_runtime", "enabled"):
            value = getattr(query, field_name)
            if value is not None and not isinstance(value, bool):
                raise FileTypeSupportFilterError(f"{field_name}_must_be_boolean")

    @staticmethod
    def _matches(row: Mapping[str, Any], query: FileTypeSupportFilter) -> bool:
        if query.priorities and row["priority"] not in query.priorities:
            return False
        if query.support_levels and row["support_level"] not in query.support_levels:
            return False
        if query.pipelines and row["pipeline"] not in query.pipelines:
            return False
        capabilities = row["capabilities"]
        if query.dimensions and not any(
            capabilities[dimension]["configured"] for dimension in query.dimensions
        ):
            return False
        selected_dimensions = query.dimensions or tuple(
            dimension.value for dimension in CapabilityDimension
        )
        parser_configured = any(
            capabilities[dimension]["configured"]
            and capabilities[dimension]["implementation"] == "parser"
            for dimension in selected_dimensions
        )
        runtime_missing = any(
            capabilities[dimension]["runtime_requirements"]
            and capabilities[dimension]["runtime_available"] is not True
            for dimension in selected_dimensions
        )
        if query.missing_parser is not None and (not parser_configured) is not query.missing_parser:
            return False
        if query.missing_runtime is not None and runtime_missing is not query.missing_runtime:
            return False
        if query.enabled is not None and row["enabled"] is not query.enabled:
            return False
        return True


def probe_file_type_runtime_requirements(
    registry: FileTypeSupportRegistry,
) -> dict[str, bool]:
    """Probe declarative requirements, including a real tree-sitter smoke parse."""

    requirements = {
        requirement
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in CapabilityDimension
        for requirement in descriptor.support_for(pipeline).capability(dimension).runtime_requirements
    }
    availability: dict[str, bool] = {}
    for requirement in sorted(requirements):
        kind, value = requirement.split(":", 1)
        if kind == "python-module":
            try:
                availability[requirement] = importlib.util.find_spec(value) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                availability[requirement] = False
        elif kind == "executable":
            availability[requirement] = shutil.which(value) is not None
        elif kind == "tree-sitter-language":
            try:
                from agent.repository_map_tree_sitter import resolve_tree_sitter_parser

                availability[requirement] = resolve_tree_sitter_parser(value).status.available
            except Exception:
                availability[requirement] = False
        else:
            availability[requirement] = False
    return availability


def parse_optional_boolean(value: str | bool | None, *, field_name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise FileTypeSupportFilterError(f"{field_name}_must_be_boolean")


@lru_cache(maxsize=1)
def get_file_type_support_service() -> FileTypeSupportService:
    return FileTypeSupportService(
        Path(__file__).resolve().parents[2],
        runtime_scope="hub_process",
    )


def _normalized_values(
    values: Iterable[str],
    *,
    transform,
) -> tuple[str, ...]:
    normalized = {
        transform(token.strip())
        for raw in values
        for token in str(raw).split(",")
        if token.strip()
    }
    return tuple(sorted(normalized))


def _reject_unknown(field_name: str, values: Iterable[str], allowed: set[str] | frozenset[str]) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise FileTypeSupportFilterError(
            f"invalid_{field_name}:{','.join(unknown)}"
        )


def _support_level(
    descriptor: FileTypeDescriptor,
    capabilities: Mapping[str, Mapping[str, Any]],
    *,
    pipeline: str,
) -> str:
    # Format-specific extraction remains domain parsing even when a safe
    # heuristic, rather than a full grammar, emits symbols or relationships.
    # ``semantic_graph`` is reserved for source-language semantics.
    if descriptor.family != "code" and pipeline == "rag_helper" and (
        capabilities[CapabilityDimension.SYMBOLS.value]["effective"]
        or capabilities[CapabilityDimension.RELATIONSHIPS.value]["effective"]
        or (
            capabilities[CapabilityDimension.INDEXED.value]["effective"]
            and capabilities[CapabilityDimension.INDEXED.value]["implementation"]
            == "parser"
        )
    ):
        return "domain_parser"
    if capabilities[CapabilityDimension.RELATIONSHIPS.value]["effective"]:
        return "semantic_graph"
    if capabilities[CapabilityDimension.SYMBOLS.value]["effective"]:
        return "symbol_index"
    indexed = capabilities[CapabilityDimension.INDEXED.value]
    if indexed["effective"]:
        return "domain_parser" if indexed["implementation"] == "parser" else "text_index"
    return "discovery" if descriptor.enabled else "unsupported"


__all__ = [
    "FileTypeSupportFilter",
    "FileTypeSupportFilterError",
    "FileTypeSupportService",
    "get_file_type_support_service",
    "parse_optional_boolean",
    "probe_file_type_runtime_requirements",
]
