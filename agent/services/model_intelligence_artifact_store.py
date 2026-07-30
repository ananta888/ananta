"""Tenant-bound, content-addressed storage for model-intelligence artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

_ARTIFACT_REF_SCHEMA = "ananta.model-intelligence-artifact-ref.v1"
_HEX_DIGEST_LENGTH = 64
_DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class ModelIntelligenceArtifactStoreError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ModelIntelligenceArtifactRef:
    """Opaque tenant-bound reference without a local storage path."""

    digest: str
    media_type: str
    size_bytes: int
    tenant_scope: str
    artifact_kind: str = "analysis"
    schema: str = _ARTIFACT_REF_SCHEMA

    def __post_init__(self) -> None:
        _require_sha256(self.digest)
        _require_hex_digest(self.tenant_scope, "artifact_tenant_scope_invalid")
        if self.schema != _ARTIFACT_REF_SCHEMA:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_ref_schema_invalid",
                "artifact reference schema is unsupported",
            )
        if not self.media_type or len(self.media_type) > 128:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_media_type_invalid",
                "artifact media type is invalid",
            )
        if not self.artifact_kind or len(self.artifact_kind) > 128:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_kind_invalid",
                "artifact kind is invalid",
            )
        if self.size_bytes < 0:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_size_invalid",
                "artifact size is invalid",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_kind": self.artifact_kind,
            "digest": self.digest,
            "media_type": self.media_type,
            "schema": self.schema,
            "size_bytes": self.size_bytes,
            "tenant_scope": self.tenant_scope,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelIntelligenceArtifactRef:
        if set(value) != {
            "artifact_kind",
            "digest",
            "media_type",
            "schema",
            "size_bytes",
            "tenant_scope",
        }:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_ref_invalid",
                "artifact reference fields are invalid",
            )
        size = value.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int):
            raise ModelIntelligenceArtifactStoreError(
                "artifact_ref_invalid",
                "artifact reference size is invalid",
            )
        return cls(
            artifact_kind=str(value.get("artifact_kind") or ""),
            digest=str(value.get("digest") or ""),
            media_type=str(value.get("media_type") or ""),
            schema=str(value.get("schema") or ""),
            size_bytes=size,
            tenant_scope=str(value.get("tenant_scope") or ""),
        )


class ModelIntelligenceArtifactStorePort(Protocol):
    def put_bytes(
        self,
        tenant_id: str,
        content: bytes,
        *,
        media_type: str,
        artifact_kind: str = "analysis",
        expected_digest: str | None = None,
    ) -> ModelIntelligenceArtifactRef: ...

    def get_bytes(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> bytes: ...

    def get_metadata(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> ModelIntelligenceArtifactRef: ...

    def delete(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> bool: ...


class FileSystemModelIntelligenceArtifactStore(ModelIntelligenceArtifactStorePort):
    """Filesystem reference implementation with per-tenant object namespaces."""

    def __init__(
        self,
        *,
        root: str | Path,
        max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        self._root = Path(root).resolve()
        self._max_artifact_bytes = max(1, int(max_artifact_bytes))
        self._root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        tenant_id: str,
        content: bytes,
        *,
        media_type: str,
        artifact_kind: str = "analysis",
        expected_digest: str | None = None,
    ) -> ModelIntelligenceArtifactRef:
        if not isinstance(content, bytes):
            raise ModelIntelligenceArtifactStoreError(
                "artifact_content_invalid",
                "artifact content must be bytes",
            )
        if len(content) > self._max_artifact_bytes:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_size_limit_exceeded",
                "artifact exceeds the configured size limit",
            )
        tenant_scope = _tenant_scope(tenant_id)
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if expected_digest is not None and not hmac.compare_digest(digest, expected_digest):
            raise ModelIntelligenceArtifactStoreError(
                "artifact_digest_mismatch",
                "artifact does not match its expected digest",
            )
        reference = ModelIntelligenceArtifactRef(
            digest=digest,
            media_type=str(media_type),
            size_bytes=len(content),
            tenant_scope=tenant_scope,
            artifact_kind=str(artifact_kind),
        )
        path = self._object_path(tenant_scope, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        _require_contained_directory(self._root, path.parent)
        if path.exists():
            self._verify_existing(path, reference)
            return reference
        self._atomic_write(path, content)
        self._verify_existing(path, reference)
        return reference

    def put_canonical_json(
        self,
        tenant_id: str,
        payload: Mapping[str, Any],
        *,
        artifact_kind: str = "analysis-json",
    ) -> ModelIntelligenceArtifactRef:
        try:
            content = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii") + b"\n"
        except (TypeError, ValueError) as exc:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_json_invalid",
                "artifact payload is not canonical JSON",
            ) from exc
        return self.put_bytes(
            tenant_id,
            content,
            media_type="application/json",
            artifact_kind=artifact_kind,
        )

    def get_bytes(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> bytes:
        self._authorize(tenant_id, reference)
        path = self._object_path(reference.tenant_scope, reference.digest)
        if path.is_symlink() or not path.is_file():
            raise ModelIntelligenceArtifactStoreError(
                "artifact_not_found",
                "artifact does not exist",
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_read_failed",
                "artifact could not be read",
            ) from exc
        self._verify_content(content, reference)
        return content

    def get_metadata(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> ModelIntelligenceArtifactRef:
        self.get_bytes(tenant_id, reference)
        return reference

    def delete(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> bool:
        """Delete one tenant-bound object; repeated deletion is a no-op."""

        self._authorize(tenant_id, reference)
        path = self._object_path(reference.tenant_scope, reference.digest)
        if not path.exists():
            return False
        self._verify_existing(path, reference)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_delete_failed",
                "artifact could not be deleted",
            ) from exc
        return True

    def _authorize(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
    ) -> None:
        expected_scope = _tenant_scope(tenant_id)
        if not hmac.compare_digest(expected_scope, reference.tenant_scope):
            raise ModelIntelligenceArtifactStoreError(
                "artifact_access_denied",
                "artifact is not available in this tenant scope",
            )

    def _object_path(self, tenant_scope: str, digest: str) -> Path:
        _require_hex_digest(tenant_scope, "artifact_tenant_scope_invalid")
        digest_hex = _require_sha256(digest)
        path = self._root / tenant_scope / "objects" / digest_hex[:2] / digest_hex
        if not path.resolve(strict=False).is_relative_to(self._root):
            raise ModelIntelligenceArtifactStoreError(
                "artifact_path_invalid",
                "artifact path escapes its storage root",
            )
        return path

    def _atomic_write(self, destination: Path, content: bytes) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=".artifact-",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination)
        except OSError as exc:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_write_failed",
                "artifact could not be written",
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _verify_existing(
        path: Path,
        reference: ModelIntelligenceArtifactRef,
    ) -> None:
        if path.is_symlink() or not path.is_file():
            raise ModelIntelligenceArtifactStoreError(
                "artifact_storage_conflict",
                "artifact object path is not a regular file",
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ModelIntelligenceArtifactStoreError(
                "artifact_read_failed",
                "artifact could not be verified",
            ) from exc
        FileSystemModelIntelligenceArtifactStore._verify_content(content, reference)

    @staticmethod
    def _verify_content(
        content: bytes,
        reference: ModelIntelligenceArtifactRef,
    ) -> None:
        actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if (
            len(content) != reference.size_bytes
            or not hmac.compare_digest(actual_digest, reference.digest)
        ):
            raise ModelIntelligenceArtifactStoreError(
                "artifact_integrity_failed",
                "artifact content failed integrity verification",
            )


def _tenant_scope(tenant_id: str) -> str:
    normalized = str(tenant_id).strip()
    if not normalized or len(normalized) > 256:
        raise ModelIntelligenceArtifactStoreError(
            "tenant_id_invalid",
            "tenant id is invalid",
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _require_sha256(value: str) -> str:
    raw = str(value)
    if not raw.startswith("sha256:"):
        raise ModelIntelligenceArtifactStoreError(
            "artifact_digest_invalid",
            "artifact digest must use sha256",
        )
    digest = raw[7:]
    _require_hex_digest(digest, "artifact_digest_invalid")
    return digest


def _require_hex_digest(value: str, reason_code: str) -> None:
    if (
        len(value) != _HEX_DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ModelIntelligenceArtifactStoreError(
            reason_code,
            "digest value is invalid",
        )


def _require_contained_directory(root: Path, directory: Path) -> None:
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise ModelIntelligenceArtifactStoreError(
            "artifact_path_invalid",
            "artifact directory is unavailable",
        ) from exc
    if not resolved.is_relative_to(root) or directory.is_symlink():
        raise ModelIntelligenceArtifactStoreError(
            "artifact_path_invalid",
            "artifact directory escapes its storage root",
        )


__all__ = [
    "FileSystemModelIntelligenceArtifactStore",
    "ModelIntelligenceArtifactRef",
    "ModelIntelligenceArtifactStoreError",
    "ModelIntelligenceArtifactStorePort",
]
