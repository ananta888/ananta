"""Common profiler parser model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class ProfileObservation:
    parser: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "profile_observation.v1"
        return payload


class ProfilerParser(Protocol):
    def parse(self, text: str) -> ProfileObservation:
        ...
