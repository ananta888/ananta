"""Fail-closed validation for externally supplied SFU evidence references.

This module deliberately has no identifier factory. The hub may only verify
references supplied by an evidence registry; it must never manufacture a
SRC_ or RUN_ identifier to make an activation decision pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping


_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_RUN_ID = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


class SourceGroundingError(ValueError):
    """Raised when the supplied registry is structurally unsafe."""


@dataclass(frozen=True)
class GroundedReference:
    """A reference already issued by an external evidence producer."""

    identifier: str
    sha256: str
    artifact_path: str | None = None

    def __post_init__(self) -> None:
        if not (_SOURCE_ID.fullmatch(self.identifier) or _RUN_ID.fullmatch(self.identifier)):
            raise SourceGroundingError("invalid_reference_identifier")
        if not _SHA256.fullmatch(self.sha256):
            raise SourceGroundingError("invalid_reference_digest")
        if self.artifact_path is not None:
            _validate_relative_artifact_path(self.artifact_path)


@dataclass(frozen=True)
class GroundingDecision:
    status: str
    reason_codes: tuple[str, ...]
    references: tuple[GroundedReference, ...]

    @property
    def accepted(self) -> bool:
        return self.status == "verified"


class SourceGroundingRegistry:
    """Immutable allow-list received from a trusted evidence input."""

    def __init__(self, references: Iterable[GroundedReference]) -> None:
        indexed: dict[str, GroundedReference] = {}
        for reference in references:
            if reference.identifier in indexed:
                raise SourceGroundingError("duplicate_registry_reference")
            indexed[reference.identifier] = reference
        self._references: Mapping[str, GroundedReference] = MappingProxyType(indexed)

    def verify(
        self,
        *,
        source_ids: Iterable[str],
        run_ids: Iterable[str],
        declared_digests: Mapping[str, str] | None = None,
        require_source: bool = True,
        require_run: bool = True,
    ) -> GroundingDecision:
        sources = tuple(source_ids)
        runs = tuple(run_ids)
        reasons: set[str] = set()

        if len(set(sources)) != len(sources):
            reasons.add("duplicate_source_reference")
        if len(set(runs)) != len(runs):
            reasons.add("duplicate_run_reference")
        if require_source and not sources:
            reasons.add("source_reference_missing")
        if require_run and not runs:
            reasons.add("run_reference_missing")

        requested = sources + runs
        resolved: list[GroundedReference] = []
        for identifier in requested:
            expected_pattern = _SOURCE_ID if identifier in sources else _RUN_ID
            if not isinstance(identifier, str) or not expected_pattern.fullmatch(identifier):
                reasons.add("reference_identifier_invalid")
                continue
            reference = self._references.get(identifier)
            if reference is None:
                reasons.add("reference_unknown")
                continue
            declared = (declared_digests or {}).get(identifier)
            if declared is not None and _normalise_digest(declared) != _normalise_digest(reference.sha256):
                reasons.add("reference_digest_mismatch")
                continue
            resolved.append(reference)

        if set(declared_digests or ()) - set(requested):
            reasons.add("unrequested_reference_digest")

        return GroundingDecision(
            status="verified" if not reasons else "rejected",
            reason_codes=tuple(sorted(reasons)),
            references=tuple(sorted(resolved, key=lambda item: item.identifier)),
        )


def _normalise_digest(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        return "invalid"
    return value.removeprefix("sha256:")


def _validate_relative_artifact_path(value: str) -> None:
    if not value or "\\" in value:
        raise SourceGroundingError("invalid_artifact_path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise SourceGroundingError("invalid_artifact_path")


__all__ = [
    "GroundedReference",
    "GroundingDecision",
    "SourceGroundingError",
    "SourceGroundingRegistry",
]
