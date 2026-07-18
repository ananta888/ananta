"""Neutral, versioned CodeCompass file-type support contract.

The contract describes classification and observable parser capabilities.  It
contains no Hub policy and grants no permission to read, index, or execute a
file.  Hub and worker containers can therefore consume the same immutable data
without depending on one another's implementations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "codecompass.file-type-support-registry.v1"
SUPPORT_MATRIX_SCHEMA_VERSION = "codecompass.file-type-support-matrix.v1"
_RUNTIME_REQUIREMENT_PATTERN = re.compile(
    r"^(?:python-module|executable|tree-sitter-language):[A-Za-z0-9_.+-]+$"
)


class FileTypeSupportContractError(ValueError):
    """Raised when schema-valid data violates a cross-field invariant."""


class CapabilityDimension(str, Enum):
    INDEXED = "indexed"
    SYMBOLS = "symbols"
    RELATIONSHIPS = "relationships"


class CapabilityImplementation(str, Enum):
    UNSUPPORTED = "unsupported"
    TEXT_FALLBACK = "text_fallback"
    HEURISTIC = "heuristic"
    PARSER = "parser"


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    implementation: CapabilityImplementation
    verified: bool
    producer: str | None
    evidence: tuple[str, ...]
    runtime_requirements: tuple[str, ...]

    @property
    def configured(self) -> bool:
        return self.implementation is not CapabilityImplementation.UNSUPPORTED

    @classmethod
    def unsupported(cls) -> "CapabilityDeclaration":
        return cls(
            implementation=CapabilityImplementation.UNSUPPORTED,
            verified=False,
            producer=None,
            evidence=(),
            runtime_requirements=(),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CapabilityDeclaration":
        return cls(
            implementation=CapabilityImplementation(str(raw["implementation"])),
            verified=bool(raw["verified"]),
            producer=str(raw["producer"]).strip() if raw.get("producer") is not None else None,
            evidence=tuple(str(item) for item in raw.get("evidence") or ()),
            runtime_requirements=tuple(str(item) for item in raw.get("runtime_requirements") or ()),
        )


@dataclass(frozen=True, slots=True)
class PipelineSupport:
    indexed: CapabilityDeclaration
    symbols: CapabilityDeclaration
    relationships: CapabilityDeclaration

    def capability(self, dimension: CapabilityDimension | str) -> CapabilityDeclaration:
        normalized = CapabilityDimension(str(getattr(dimension, "value", dimension)))
        return {
            CapabilityDimension.INDEXED: self.indexed,
            CapabilityDimension.SYMBOLS: self.symbols,
            CapabilityDimension.RELATIONSHIPS: self.relationships,
        }[normalized]

    @classmethod
    def unsupported(cls) -> "PipelineSupport":
        unsupported = CapabilityDeclaration.unsupported()
        return cls(indexed=unsupported, symbols=unsupported, relationships=unsupported)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PipelineSupport":
        return cls(
            indexed=CapabilityDeclaration.from_mapping(raw[CapabilityDimension.INDEXED.value]),
            symbols=CapabilityDeclaration.from_mapping(raw[CapabilityDimension.SYMBOLS.value]),
            relationships=CapabilityDeclaration.from_mapping(raw[CapabilityDimension.RELATIONSHIPS.value]),
        )


@dataclass(frozen=True, slots=True)
class FileTypeSelectors:
    exact_filenames: tuple[str, ...]
    filename_patterns: tuple[str, ...]
    compound_suffixes: tuple[str, ...]
    extensions: tuple[str, ...]
    shebang_patterns: tuple[str, ...]
    text_fallback: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FileTypeSelectors":
        return cls(
            exact_filenames=tuple(str(item) for item in raw.get("exact_filenames") or ()),
            filename_patterns=tuple(str(item) for item in raw.get("filename_patterns") or ()),
            compound_suffixes=tuple(str(item) for item in raw.get("compound_suffixes") or ()),
            extensions=tuple(str(item) for item in raw.get("extensions") or ()),
            shebang_patterns=tuple(str(item) for item in raw.get("shebang_patterns") or ()),
            text_fallback=bool(raw.get("text_fallback", False)),
        )

    @property
    def empty(self) -> bool:
        return not (
            self.exact_filenames
            or self.filename_patterns
            or self.compound_suffixes
            or self.extensions
            or self.shebang_patterns
            or self.text_fallback
        )


@dataclass(frozen=True, slots=True)
class FileTypeDescriptor:
    format_id: str
    display_name: str
    family: str
    priority: str
    enabled: bool
    match_priority: int | None
    selectors: FileTypeSelectors
    security_class: str
    parser_strategy: str
    fallback_strategy: str
    known_limits: tuple[str, ...]
    pipeline_support: Mapping[str, PipelineSupport]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FileTypeDescriptor":
        support = {
            str(pipeline): PipelineSupport.from_mapping(payload)
            for pipeline, payload in dict(raw.get("pipeline_support") or {}).items()
        }
        return cls(
            format_id=str(raw["format_id"]),
            display_name=str(raw["display_name"]),
            family=str(raw["family"]),
            priority=str(raw["priority"]),
            enabled=bool(raw["enabled"]),
            match_priority=int(raw["match_priority"]) if "match_priority" in raw else None,
            selectors=FileTypeSelectors.from_mapping(raw["selectors"]),
            security_class=str(raw["security_class"]),
            parser_strategy=str(raw["parser_strategy"]),
            fallback_strategy=str(raw["fallback_strategy"]),
            known_limits=tuple(str(item) for item in raw.get("known_limits") or ()),
            pipeline_support=MappingProxyType(support),
        )

    def support_for(self, pipeline: str) -> PipelineSupport:
        return self.pipeline_support.get(str(pipeline), PipelineSupport.unsupported())


class FileTypeSupportRegistry:
    """Validated immutable registry with deterministic matrix export.

    ``enabled`` only controls discovery by the classifier.  It is deliberately
    not an allow-list and must never replace path, secret, or authorization
    policy checks in a consuming pipeline.
    """

    def __init__(
        self,
        *,
        registry_version: str,
        pipelines: Sequence[str],
        descriptors: Sequence[FileTypeDescriptor],
        source_mapping: Mapping[str, Any],
    ) -> None:
        self.registry_version = str(registry_version)
        self.pipelines = tuple(str(item) for item in pipelines)
        self.descriptors = tuple(sorted(descriptors, key=lambda item: item.format_id))
        self._by_id = MappingProxyType({item.format_id: item for item in self.descriptors})
        self._source_mapping = json.loads(json.dumps(source_mapping))
        self._validate_semantics()

    @classmethod
    def load(cls, registry_path: Path, *, schema_path: Path) -> "FileTypeSupportRegistry":
        schema = _read_json_object(schema_path)
        Draft202012Validator.check_schema(schema)
        payload = _read_json_object(registry_path)
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
        if errors:
            readable = "; ".join(
                f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors
            )
            raise FileTypeSupportContractError(f"invalid file-type registry {registry_path}: {readable}")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FileTypeSupportRegistry":
        if str(raw.get("schema") or "") != SCHEMA_VERSION:
            raise FileTypeSupportContractError(f"unsupported file-type registry schema: {raw.get('schema')!r}")
        dimensions = tuple(str(item) for item in raw.get("support_dimensions") or ())
        expected = tuple(item.value for item in CapabilityDimension)
        if dimensions != expected:
            raise FileTypeSupportContractError(f"support_dimensions must be {expected!r}")
        return cls(
            registry_version=str(raw.get("registry_version") or ""),
            pipelines=tuple(str(item) for item in raw.get("pipelines") or ()),
            descriptors=tuple(FileTypeDescriptor.from_mapping(item) for item in raw.get("formats") or ()),
            source_mapping=raw,
        )

    @property
    def digest(self) -> str:
        canonical = json.dumps(self._source_mapping, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def descriptor(self, format_id: str) -> FileTypeDescriptor | None:
        return self._by_id.get(str(format_id))

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._source_mapping))

    def support_matrix(
        self,
        *,
        runtime_availability: Mapping[str, bool] | None = None,
    ) -> dict[str, Any]:
        runtime = dict(runtime_availability or {})
        rows: list[dict[str, Any]] = []
        for descriptor in self.descriptors:
            for pipeline in self.pipelines:
                support = descriptor.support_for(pipeline)
                capability_payload = {
                    dimension.value: self._capability_matrix_entry(
                        support.capability(dimension),
                        runtime_availability=runtime,
                    )
                    for dimension in CapabilityDimension
                }
                effective_level = "unsupported"
                for dimension in CapabilityDimension:
                    if capability_payload[dimension.value]["effective"]:
                        effective_level = dimension.value
                rows.append(
                    {
                        "format_id": descriptor.format_id,
                        "display_name": descriptor.display_name,
                        "family": descriptor.family,
                        "priority": descriptor.priority,
                        "enabled": descriptor.enabled,
                        "pipeline": pipeline,
                        "effective_level": effective_level,
                        "capabilities": capability_payload,
                    }
                )
        return {
            "schema": SUPPORT_MATRIX_SCHEMA_VERSION,
            "registry_version": self.registry_version,
            "registry_digest": self.digest,
            "support_dimensions": [item.value for item in CapabilityDimension],
            "rows": rows,
        }

    @staticmethod
    def _capability_matrix_entry(
        declaration: CapabilityDeclaration,
        *,
        runtime_availability: Mapping[str, bool],
    ) -> dict[str, Any]:
        if not declaration.configured:
            available: bool | None = False
        elif not declaration.runtime_requirements:
            available = True
        elif all(requirement in runtime_availability for requirement in declaration.runtime_requirements):
            available = all(runtime_availability[requirement] for requirement in declaration.runtime_requirements)
        else:
            available = None
        return {
            "configured": declaration.configured,
            "runtime_available": available,
            "verified": declaration.verified,
            "effective": bool(declaration.configured and declaration.verified and available is True),
            "implementation": declaration.implementation.value,
            "producer": declaration.producer,
            "evidence": list(declaration.evidence),
            "runtime_requirements": list(declaration.runtime_requirements),
        }

    def _validate_semantics(self) -> None:
        if not self.registry_version:
            raise FileTypeSupportContractError("registry_version must not be empty")
        if len(set(self.pipelines)) != len(self.pipelines):
            raise FileTypeSupportContractError("pipelines must be unique")
        if len(self._by_id) != len(self.descriptors):
            raise FileTypeSupportContractError("format_id values must be unique")

        known_pipelines = set(self.pipelines)
        selectors: dict[tuple[str, str], list[FileTypeDescriptor]] = {}
        for descriptor in self.descriptors:
            if descriptor.selectors.empty:
                raise FileTypeSupportContractError(f"format {descriptor.format_id} has no selector")
            unknown_pipelines = set(descriptor.pipeline_support) - known_pipelines
            if unknown_pipelines:
                raise FileTypeSupportContractError(
                    f"format {descriptor.format_id} references unknown pipelines: {sorted(unknown_pipelines)}"
                )
            self._validate_descriptor_support(descriptor)
            self._collect_selectors(descriptor, selectors)

        for (selector_kind, selector), owners in sorted(selectors.items()):
            active = [owner for owner in owners if owner.enabled]
            if len(active) < 2:
                continue
            priorities = [owner.match_priority for owner in active]
            if any(priority is None for priority in priorities) or len(set(priorities)) != len(priorities):
                owner_ids = ", ".join(owner.format_id for owner in active)
                raise FileTypeSupportContractError(
                    f"ambiguous active {selector_kind} selector {selector!r} owned by {owner_ids}; "
                    "set distinct explicit match_priority values"
                )

    @staticmethod
    def _validate_descriptor_support(descriptor: FileTypeDescriptor) -> None:
        for pipeline, support in descriptor.pipeline_support.items():
            if support.symbols.configured and not support.indexed.configured:
                raise FileTypeSupportContractError(
                    f"{descriptor.format_id}/{pipeline}: symbols require indexed support"
                )
            if support.relationships.configured and not support.symbols.configured:
                raise FileTypeSupportContractError(
                    f"{descriptor.format_id}/{pipeline}: relationships require symbol support"
                )
            if support.symbols.verified and not support.indexed.verified:
                raise FileTypeSupportContractError(
                    f"{descriptor.format_id}/{pipeline}: verified symbols require verified indexed support"
                )
            if support.relationships.verified and not support.symbols.verified:
                raise FileTypeSupportContractError(
                    f"{descriptor.format_id}/{pipeline}: verified relationships require verified symbols"
                )
            for dimension in CapabilityDimension:
                declaration = support.capability(dimension)
                if declaration.configured:
                    if not declaration.producer or not declaration.evidence:
                        raise FileTypeSupportContractError(
                            f"{descriptor.format_id}/{pipeline}/{dimension.value}: configured support "
                            "requires producer and evidence"
                        )
                    if declaration.verified and not any(_is_test_ref(ref) for ref in declaration.evidence):
                        raise FileTypeSupportContractError(
                            f"{descriptor.format_id}/{pipeline}/{dimension.value}: verified support "
                            "requires test evidence"
                        )
                elif declaration.producer or declaration.evidence or declaration.runtime_requirements:
                    raise FileTypeSupportContractError(
                        f"{descriptor.format_id}/{pipeline}/{dimension.value}: unsupported capability "
                        "cannot declare producer, evidence, or runtime requirements"
                    )
                for evidence_ref in declaration.evidence:
                    _validate_relative_ref(evidence_ref, field_name="evidence")
                for requirement in declaration.runtime_requirements:
                    if _RUNTIME_REQUIREMENT_PATTERN.fullmatch(requirement) is None:
                        raise FileTypeSupportContractError(
                            f"{descriptor.format_id}/{pipeline}/{dimension.value}: "
                            f"invalid runtime requirement {requirement!r}"
                        )

    @staticmethod
    def _collect_selectors(
        descriptor: FileTypeDescriptor,
        selectors: dict[tuple[str, str], list[FileTypeDescriptor]],
    ) -> None:
        values_by_kind = {
            "exact_filename": descriptor.selectors.exact_filenames,
            "filename_pattern": descriptor.selectors.filename_patterns,
            "compound_suffix": descriptor.selectors.compound_suffixes,
            "extension": descriptor.selectors.extensions,
            "shebang": descriptor.selectors.shebang_patterns,
        }
        for kind, values in values_by_kind.items():
            for value in values:
                normalized = value.lower() if kind in {"compound_suffix", "extension"} else value
                selectors.setdefault((kind, normalized), []).append(descriptor)
                if kind in {"compound_suffix", "extension"} and value != value.lower():
                    raise FileTypeSupportContractError(
                        f"{descriptor.format_id}: {kind} selectors must be lower-case: {value!r}"
                    )
                if kind == "exact_filename" and PurePosixPath(value).name != value:
                    raise FileTypeSupportContractError(
                        f"{descriptor.format_id}: exact_filenames must contain basenames only: {value!r}"
                    )
                if kind == "shebang":
                    try:
                        re.compile(value)
                    except re.error as exc:
                        raise FileTypeSupportContractError(
                            f"{descriptor.format_id}: invalid shebang pattern {value!r}"
                        ) from exc
        if descriptor.selectors.text_fallback:
            selectors.setdefault(("text_fallback", "*"), []).append(descriptor)


def load_file_type_support_registry(
    repository_root: Path,
    *,
    registry_path: Path | None = None,
    schema_path: Path | None = None,
) -> FileTypeSupportRegistry:
    """Load the repository registry without introducing an agent-layer import."""

    root = Path(repository_root).resolve()
    return FileTypeSupportRegistry.load(
        registry_path or root / "config" / "codecompass" / "file_type_support.v1.json",
        schema_path=schema_path
        or root / "schemas" / "codecompass" / "file_type_support_registry.v1.json",
    )


def _validate_relative_ref(value: str, *, field_name: str) -> None:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise FileTypeSupportContractError(f"{field_name} reference must be repository-relative: {value!r}")


def _is_test_ref(value: str) -> bool:
    parts = PurePosixPath(str(value).replace("\\", "/")).parts
    return "tests" in parts


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileTypeSupportContractError(f"cannot load JSON object {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FileTypeSupportContractError(f"expected JSON object in {path}")
    return payload
