"""Versioned, immutable model capability wire contract.

This module contains data validation only. It has no registry, persistence or
runtime imports and is therefore safe to package into multiple containers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "ananta.model-capability.v1"


class ModelStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


def _bounded_strings(values: Sequence[object], *, field_name: str, maximum: int = 64) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence")
    normalized = tuple(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} entries")
    return normalized


@dataclass(frozen=True)
class ModelCapability:
    id: str
    engine: str
    revision: str
    tasks: tuple[str, ...]
    languages: tuple[str, ...]
    device: str
    quantization: str
    license: str
    status: ModelStatus
    manifest_digest: str
    extensions: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported model capability schema")
        for name, value in (
            ("id", self.id),
            ("engine", self.engine),
            ("revision", self.revision),
            ("device", self.device),
            ("quantization", self.quantization),
            ("license", self.license),
            ("manifest_digest", self.manifest_digest),
        ):
            if not value or len(value) > 256:
                raise ValueError(f"{name} must be non-empty and bounded")
        if not self.tasks:
            raise ValueError("tasks must not be empty")
        unknown_extensions = set(self.extensions) - {"voice", "restricted_inference"}
        if unknown_extensions:
            raise ValueError(f"unknown capability extensions: {sorted(unknown_extensions)}")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ModelCapability":
        try:
            status = ModelStatus(str(raw.get("status") or ""))
        except ValueError as exc:
            raise ValueError("status must be ready, degraded, or unavailable") from exc
        raw_extensions = raw.get("extensions")
        extensions = {
            str(key): dict(value)
            for key, value in (raw_extensions.items() if isinstance(raw_extensions, Mapping) else ())
            if isinstance(value, Mapping)
        }
        return cls(
            schema_version=str(raw.get("schema_version") or ""),
            id=str(raw.get("id") or "").strip(),
            engine=str(raw.get("engine") or "").strip(),
            revision=str(raw.get("revision") or "").strip(),
            tasks=_bounded_strings(raw.get("tasks") or (), field_name="tasks"),
            languages=_bounded_strings(raw.get("languages") or (), field_name="languages"),
            device=str(raw.get("device") or "").strip(),
            quantization=str(raw.get("quantization") or "none").strip(),
            license=str(raw.get("license") or "").strip(),
            status=status,
            manifest_digest=str(raw.get("manifest_digest") or "").strip(),
            extensions=extensions,
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["tasks"] = list(self.tasks)
        payload["languages"] = list(self.languages)
        payload["extensions"] = {key: dict(value) for key, value in self.extensions.items()}
        return payload
