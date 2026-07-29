"""Bounded, fail-closed loading for Hub-approved vector-index input refs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocationError,
    VectorIndexArtifactLocator,
    VectorIndexArtifactScope,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_MAXIMUM_BYTES = 64 * 1024 * 1024
_DEFAULT_MAXIMUM_POINTS = 100_000


class VectorIndexInputError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "vector_index_input_invalid")
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class VectorIndexInputReference:
    """A relative artifact reference resolved only inside Worker-owned roots."""

    path: str
    sha256: str | None = None
    scope_fingerprint: str | None = None

    def __post_init__(self) -> None:
        raw_path = str(self.path or "").strip().replace("\\", "/")
        if not raw_path or len(raw_path) > 512 or "\x00" in raw_path:
            raise VectorIndexInputError("vector_index_input_ref_path_invalid")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise VectorIndexInputError("vector_index_input_ref_path_invalid")
        digest = str(self.sha256 or "").strip().lower() or None
        if digest is not None and _SHA256.fullmatch(digest) is None:
            raise VectorIndexInputError("vector_index_input_ref_sha256_invalid")
        scope_fingerprint = str(self.scope_fingerprint or "").strip().lower() or None
        if scope_fingerprint is not None and _SHA256.fullmatch(scope_fingerprint) is None:
            raise VectorIndexInputError("vector_index_input_ref_scope_fingerprint_invalid")
        object.__setattr__(self, "path", candidate.as_posix())
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(
            self,
            "scope_fingerprint",
            scope_fingerprint,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        require_sha256: bool = False,
        require_scope_fingerprint: bool = False,
    ) -> "VectorIndexInputReference":
        payload = dict(value or {})
        if set(payload) - {"path", "sha256", "scope_fingerprint"}:
            raise VectorIndexInputError("vector_index_input_ref_fields_forbidden")
        reference = cls(
            path=str(payload.get("path") or ""),
            sha256=(str(payload["sha256"]) if payload.get("sha256") is not None else None),
            scope_fingerprint=(
                str(payload["scope_fingerprint"]) if payload.get("scope_fingerprint") is not None else None
            ),
        )
        if require_sha256 and reference.sha256 is None:
            raise VectorIndexInputError("vector_index_input_ref_sha256_required")
        if require_scope_fingerprint and reference.scope_fingerprint is None:
            raise VectorIndexInputError("vector_index_input_ref_scope_fingerprint_required")
        return reference

    def to_dict(self) -> dict[str, str]:
        result = {"path": self.path}
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        if self.scope_fingerprint is not None:
            result["scope_fingerprint"] = self.scope_fingerprint
        return result

    def validate_binding(
        self,
        trusted_scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> None:
        """Verify digest, full scope fingerprint and canonical relative path."""

        if self.sha256 is None:
            raise VectorIndexInputError("vector_index_input_ref_sha256_required")
        if self.scope_fingerprint is None:
            raise VectorIndexInputError("vector_index_input_ref_scope_fingerprint_required")
        try:
            VectorIndexArtifactLocator.verify_reference(
                scope=trusted_scope,
                path=self.path,
                content_sha256=self.sha256,
                scope_fingerprint=self.scope_fingerprint,
            )
        except VectorIndexArtifactLocationError as exc:
            raise VectorIndexInputError(exc.reason) from exc


class BoundedVectorIndexInputLoader:
    """Resolve and read immutable task inputs without escaping configured roots."""

    def __init__(
        self,
        *,
        allowed_roots: Sequence[str | Path] | None = None,
        maximum_bytes: int = _DEFAULT_MAXIMUM_BYTES,
        maximum_points: int = _DEFAULT_MAXIMUM_POINTS,
    ) -> None:
        if allowed_roots is None:
            configured = [
                value
                for value in str(os.environ.get("ANANTA_VECTOR_INDEX_INPUT_ROOTS") or "").split(os.pathsep)
                if value.strip()
            ]
            allowed_roots = configured or (Path("/app/data/vector-index-inputs"),)
        roots = tuple(Path(root).resolve(strict=False) for root in allowed_roots)
        if not roots:
            raise ValueError("vector_index_input_roots_required")
        self._allowed_roots = roots
        self._maximum_bytes = max(1, int(maximum_bytes))
        self._maximum_points = max(1, int(maximum_points))

    def load_bytes(
        self,
        value: Mapping[str, Any],
        *,
        trusted_scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> bytes:
        reference = self.validate_reference(
            value,
            trusted_scope=trusted_scope,
        )
        source = self._resolve(reference)
        try:
            with source.open("rb") as handle:
                raw = handle.read(self._maximum_bytes + 1)
        except OSError as exc:
            raise VectorIndexInputError("vector_index_input_ref_unreadable") from exc
        if len(raw) > self._maximum_bytes:
            raise VectorIndexInputError("vector_index_input_ref_too_large")
        if reference.sha256 is not None:
            digest = hashlib.sha256(raw).hexdigest()
            if digest != reference.sha256:
                raise VectorIndexInputError("vector_index_input_ref_digest_mismatch")
        return raw

    def load_points(
        self,
        value: Mapping[str, Any],
        *,
        trusted_scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        raw = self.load_bytes(value, trusted_scope=trusted_scope)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VectorIndexInputError("vector_index_input_ref_json_invalid") from exc
        candidates = payload.get("points") if isinstance(payload, Mapping) else payload
        if not isinstance(candidates, list) or any(not isinstance(item, Mapping) for item in candidates):
            raise VectorIndexInputError("vector_index_input_ref_points_invalid")
        if len(candidates) > self._maximum_points:
            raise VectorIndexInputError("vector_index_input_ref_points_limit_exceeded")
        return tuple(dict(item) for item in candidates)

    def load_document_input(
        self,
        value: Mapping[str, Any],
        *,
        trusted_scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Load a bounded, typed document bundle for Worker-side embedding."""

        raw = self.load_bytes(value, trusted_scope=trusted_scope)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VectorIndexInputError("vector_index_input_ref_json_invalid") from exc
        if not isinstance(payload, Mapping):
            raise VectorIndexInputError("vector_index_input_ref_documents_invalid")
        if set(payload) != {"schema", "kind", "documents"}:
            raise VectorIndexInputError("vector_index_input_ref_document_fields_invalid")
        documents = payload.get("documents")
        if not isinstance(documents, list) or any(not isinstance(item, Mapping) for item in documents):
            raise VectorIndexInputError("vector_index_input_ref_documents_invalid")
        if not documents:
            raise VectorIndexInputError("vector_index_input_ref_documents_required")
        if len(documents) > self._maximum_points:
            raise VectorIndexInputError("vector_index_input_ref_documents_limit_exceeded")
        return {
            "schema": str(payload.get("schema") or ""),
            "kind": str(payload.get("kind") or ""),
            "documents": tuple(dict(item) for item in documents),
        }

    @staticmethod
    def validate_reference(
        value: Mapping[str, Any],
        *,
        trusted_scope: VectorIndexArtifactScope | Mapping[str, Any],
    ) -> VectorIndexInputReference:
        """Validate the complete immutable binding before any filesystem I/O."""

        if trusted_scope is None:
            raise VectorIndexInputError("vector_index_input_ref_scope_required")
        reference = VectorIndexInputReference.from_mapping(
            value,
            require_sha256=True,
            require_scope_fingerprint=True,
        )
        reference.validate_binding(trusted_scope)
        return reference

    def _resolve(self, reference: VectorIndexInputReference) -> Path:
        relative = Path(*PurePosixPath(reference.path).parts)
        for root in self._allowed_roots:
            candidate = root / relative
            if self._contains_symlink(root, relative):
                raise VectorIndexInputError("vector_index_input_ref_symlink_forbidden")
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_relative_to(root):
                continue
            if not resolved.is_file():
                raise VectorIndexInputError("vector_index_input_ref_not_regular_file")
            return resolved
        raise VectorIndexInputError("vector_index_input_ref_not_found")

    @staticmethod
    def _contains_symlink(root: Path, relative: Path) -> bool:
        current = root
        if current.is_symlink():
            return True
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False


__all__ = [
    "BoundedVectorIndexInputLoader",
    "VectorIndexInputError",
    "VectorIndexInputReference",
]
