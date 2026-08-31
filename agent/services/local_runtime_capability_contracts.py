"""Closed contracts for evidence-bound local model runtime capabilities."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,511}$")
_CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
CAPABILITY_NAMES = frozenset(
    {"chat", "completion", "tools", "vision", "thinking", "embedding", "streaming"}
)
CAPABILITY_SOURCES = frozenset(
    {
        "runtime_reported",
        "profile_declared",
        "observed_success",
        "observed_failure",
        "heuristic",
    }
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("local_runtime_capability_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("local_runtime_capability_timestamp_invalid")
    return parsed.astimezone(UTC)


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeCapabilityClaim:
    name: str
    supported: bool
    source: str
    confidence: float
    discovered_at: str
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if _CAPABILITY.fullmatch(self.name) is None or self.source not in CAPABILITY_SOURCES:
            raise ValueError("local_runtime_capability_claim_invalid")
        if type(self.supported) is not bool or not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("local_runtime_capability_claim_invalid")
        if not self.discovered_at or len(self.discovered_at) > 64 or (self.expires_at and len(self.expires_at) > 64):
            raise ValueError("local_runtime_capability_claim_invalid")
        discovered = _timestamp(self.discovered_at)
        if self.expires_at is not None and _timestamp(self.expires_at) <= discovered:
            raise ValueError("local_runtime_capability_expiry_invalid")
        if self.source == "heuristic" and self.supported and self.confidence >= 1.0:
            raise ValueError("local_runtime_heuristic_confidence_invalid")
        if self.source == "observed_failure" and self.supported:
            raise ValueError("local_runtime_observed_failure_invalid")

    def active(self, *, at: datetime | None = None) -> bool:
        if self.expires_at is None:
            return True
        return _timestamp(self.expires_at) > (at or datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeModelSnapshot:
    provider_id: str
    model_id: str
    model_digest: str
    runtime_version: str
    model_kind: str
    context_window: int | None
    template_family: str
    template_sha256: str | None
    capabilities: tuple[RuntimeCapabilityClaim, ...]
    conflicts: tuple[str, ...]
    discovered_at: str
    stale: bool = False

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.provider_id) or not _IDENTIFIER.fullmatch(self.model_id):
            raise ValueError("local_runtime_snapshot_binding_invalid")
        if type(self.stale) is not bool:
            raise ValueError("local_runtime_snapshot_stale_invalid")
        if not _DIGEST.fullmatch(self.model_digest):
            raise ValueError("local_runtime_snapshot_digest_invalid")
        if not self.runtime_version.strip() or len(self.runtime_version) > 128:
            raise ValueError("local_runtime_snapshot_version_invalid")
        if self.model_kind not in {"chat", "embedding", "unknown"}:
            raise ValueError("local_runtime_snapshot_kind_invalid")
        if self.context_window is not None and (
            type(self.context_window) is not int
            or not 1 <= self.context_window <= 100_000_000
        ):
            raise ValueError("local_runtime_snapshot_context_invalid")
        if self.template_family not in {"chatml", "llama3", "mistral", "gemma", "hermes", "phi", "unknown", "conflict"}:
            raise ValueError("local_runtime_template_family_invalid")
        if self.template_sha256 is not None and not _DIGEST.fullmatch(self.template_sha256):
            raise ValueError("local_runtime_template_digest_invalid")
        bindings = [(claim.name, claim.source) for claim in self.capabilities]
        if len(bindings) != len(set(bindings)) or any(
            not str(item).strip() or len(item) > 160 for item in self.conflicts
        ):
            raise ValueError("local_runtime_snapshot_capabilities_invalid")

    @property
    def snapshot_sha256(self) -> str:
        return canonical_sha256(self.to_dict(include_snapshot_digest=False))

    def claim(self, name: str) -> RuntimeCapabilityClaim | None:
        precedence = {
            "observed_failure": 0,
            "runtime_reported": 1,
            "observed_success": 2,
            "profile_declared": 3,
            "heuristic": 4,
        }
        candidates = [item for item in self.capabilities if item.name == name and item.active()]
        return min(candidates, key=lambda item: precedence[item.source], default=None)

    def claims(self, name: str) -> tuple[RuntimeCapabilityClaim, ...]:
        return tuple(item for item in self.capabilities if item.name == name)

    def routable(self, capability: str) -> bool:
        if capability not in CAPABILITY_NAMES:
            return False
        claims = tuple(item for item in self.claims(capability) if item.active())
        if (
            not claims
            or self.stale
            or self.model_kind == "embedding" and capability == "chat"
        ):
            return False
        if any(
            item.source == "observed_failure"
            or (item.source == "runtime_reported" and not item.supported)
            for item in claims
        ):
            return False
        if capability == "tools" and self.template_family == "conflict":
            return False
        return any(
            item.supported
            and item.source in {"runtime_reported", "profile_declared", "observed_success"}
            for item in claims
        )

    def to_dict(self, *, include_snapshot_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "ananta.local-runtime-model-snapshot.v1",
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_digest": self.model_digest,
            "runtime_version": self.runtime_version,
            "model_kind": self.model_kind,
            "context_window": self.context_window,
            "template_family": self.template_family,
            "template_sha256": self.template_sha256,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "conflicts": list(self.conflicts),
            "discovered_at": self.discovered_at,
            "stale": self.stale,
        }
        if include_snapshot_digest:
            payload["snapshot_sha256"] = self.snapshot_sha256
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RuntimeModelSnapshot":
        if raw.get("schema") != "ananta.local-runtime-model-snapshot.v1":
            raise ValueError("local_runtime_snapshot_schema_invalid")
        capabilities = raw.get("capabilities")
        if not isinstance(capabilities, list) or len(capabilities) > 128:
            raise ValueError("local_runtime_snapshot_capabilities_invalid")
        claims = tuple(RuntimeCapabilityClaim(**dict(item)) for item in capabilities if isinstance(item, Mapping))
        if len(claims) != len(capabilities):
            raise ValueError("local_runtime_snapshot_capabilities_invalid")
        snapshot = cls(
            provider_id=str(raw.get("provider_id") or ""),
            model_id=str(raw.get("model_id") or ""),
            model_digest=str(raw.get("model_digest") or ""),
            runtime_version=str(raw.get("runtime_version") or ""),
            model_kind=str(raw.get("model_kind") or "unknown"),
            context_window=raw.get("context_window"),
            template_family=str(raw.get("template_family") or "unknown"),
            template_sha256=raw.get("template_sha256"),
            capabilities=claims,
            conflicts=tuple(str(item) for item in raw.get("conflicts") or ()),
            discovered_at=str(raw.get("discovered_at") or ""),
            stale=raw.get("stale", False),
        )
        supplied = raw.get("snapshot_sha256")
        if supplied is not None and supplied != snapshot.snapshot_sha256:
            raise ValueError("local_runtime_snapshot_content_digest_mismatch")
        return snapshot


__all__ = [
    "CAPABILITY_NAMES",
    "RuntimeCapabilityClaim",
    "RuntimeModelSnapshot",
    "canonical_sha256",
    "utc_now",
]
