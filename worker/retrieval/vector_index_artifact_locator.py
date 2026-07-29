"""Pure canonical locator for scope-bound vector-index input artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SCOPE_FIELDS = (
    "workspace_id",
    "repository_id",
    "profile_name",
    "domain",
)


class VectorIndexArtifactScope(Protocol):
    """Structural scope contract shared by Hub and Worker boundaries."""

    workspace_id: str
    repository_id: str
    profile_name: str
    domain: str


class VectorIndexArtifactLocationError(ValueError):
    """Stable fail-closed error raised by the pure locator."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "vector_index_input_ref_binding_invalid")
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class VectorIndexArtifactLocation:
    """Canonical identity and relative path for one immutable input."""

    path: str
    sha256: str
    scope_fingerprint: str

    def to_reference(self) -> dict[str, str]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "scope_fingerprint": self.scope_fingerprint,
        }


class VectorIndexArtifactLocator:
    """Derive and verify vector-index artifact identities without I/O."""

    @classmethod
    def locate(
        cls,
        *,
        scope: VectorIndexArtifactScope | Mapping[str, Any],
        content_sha256: str,
    ) -> VectorIndexArtifactLocation:
        digest = str(content_sha256 or "").strip().lower()
        if _SHA256.fullmatch(digest) is None:
            raise VectorIndexArtifactLocationError("vector_index_input_ref_sha256_invalid")
        scope_payload = cls.scope_payload(scope)
        fingerprint = hashlib.sha256(
            json.dumps(
                scope_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        return VectorIndexArtifactLocation(
            path=f"{scope_payload['domain']}/{fingerprint}/{digest}.json",
            sha256=digest,
            scope_fingerprint=fingerprint,
        )

    @classmethod
    def scope_fingerprint(
        cls,
        scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> str:
        return cls.locate(scope=scope, content_sha256="0" * 64).scope_fingerprint

    @classmethod
    def verify_reference(
        cls,
        *,
        scope: VectorIndexArtifactScope | Mapping[str, Any],
        path: str,
        content_sha256: str,
        scope_fingerprint: str,
    ) -> VectorIndexArtifactLocation:
        expected = cls.locate(
            scope=scope,
            content_sha256=content_sha256,
        )
        supplied_fingerprint = str(scope_fingerprint or "").strip().lower()
        if _SHA256.fullmatch(supplied_fingerprint) is None:
            raise VectorIndexArtifactLocationError("vector_index_input_ref_scope_fingerprint_invalid")
        if supplied_fingerprint != expected.scope_fingerprint:
            raise VectorIndexArtifactLocationError("vector_index_input_ref_scope_mismatch")
        if str(path or "").strip().replace("\\", "/") != expected.path:
            raise VectorIndexArtifactLocationError("vector_index_input_ref_path_mismatch")
        return expected

    @staticmethod
    def scope_payload(
        scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> dict[str, str]:
        if isinstance(scope, Mapping):
            raw = {field: scope.get(field) for field in _SCOPE_FIELDS}
        else:
            raw = {field: getattr(scope, field, None) for field in _SCOPE_FIELDS}
        payload: dict[str, str] = {}
        for field in _SCOPE_FIELDS:
            value = raw.get(field)
            normalized = str(value or "").strip()
            if _SCOPE_VALUE.fullmatch(normalized) is None:
                raise VectorIndexArtifactLocationError(f"vector_index_input_ref_{field}_invalid")
            payload[field] = normalized
        return payload


__all__ = [
    "VectorIndexArtifactLocation",
    "VectorIndexArtifactLocationError",
    "VectorIndexArtifactLocator",
    "VectorIndexArtifactScope",
]
