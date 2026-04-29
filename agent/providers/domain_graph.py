from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .interfaces import ProviderDescriptor, ProviderHealthReport


@dataclass(frozen=True)
class DomainGraphIngestRequest:
    source_ref: str
    source_kind: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainGraphIngestResult:
    artifact: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class DomainGraphProvider(Protocol):
    descriptor: ProviderDescriptor

    def health(self) -> ProviderHealthReport: ...

    def ingest(self, request: DomainGraphIngestRequest) -> DomainGraphIngestResult: ...
