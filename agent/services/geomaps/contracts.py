"""Immutable contracts shared by GeoMap joins and renderer adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class GeoMapError(ValueError):
    """Bounded domain error suitable for a machine-readable Hub response."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class RegionValue:
    region_id: str
    name: str
    value: float
    source_rows: int


@dataclass(frozen=True)
class JoinReport:
    matched: tuple[str, ...]
    unmatched: tuple[str, ...]
    duplicates: tuple[str, ...]
    missing_geometry: tuple[str, ...]
    invalid_values: tuple[str, ...]
    match_ratio: float
    minimum_match_ratio: float
    publication_eligible: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class GeoMapProjection:
    schema: str
    map_id: str
    registry_version: int
    aggregation: str
    values: tuple[RegionValue, ...]
    report: JoinReport
    map_attribution: str
    data_attribution: str

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GeoMapExportArtifact:
    filename: str
    media_type: str
    content: bytes
    metadata: dict[str, Any]
