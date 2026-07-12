"""Filesystem admission for immutable restricted-inference snapshots.

The worker resolves a bounded manifest by identifier, derives the immutable
snapshot directory from its digest, and verifies every declared byte before an
adapter can be constructed.  No network access or model deserialization occurs
in this layer.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from agent.services.restricted_inference_model_manifest import (
    ModelManifestValidationError,
    ModelSnapshotValidator,
    RestrictedModelManifest,
    SnapshotValidationPolicy,
    VerifiedModelSnapshot,
)
from ananta_contracts.model_capability import ModelCapability, ModelStatus

_MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,191}$")


class FilesystemSnapshotAdmission:
    """Admit only ``<snapshot_root>/<manifest-digest>`` snapshots.

    Manifest files are read with ``O_NOFOLLOW`` where available and are size
    bounded.  Snapshot verification is deliberately repeated for admission;
    the lazy adapter registry then deduplicates expensive model loading by
    digest.
    """

    def __init__(
        self,
        *,
        manifest_root: str | Path,
        snapshot_root: str | Path,
        validation_policy: SnapshotValidationPolicy | None = None,
        max_manifest_bytes: int = 1024 * 1024,
    ) -> None:
        self._manifest_root = _safe_directory(manifest_root, "manifest_root")
        self._snapshot_root = _safe_directory(snapshot_root, "snapshot_root")
        if not 1024 <= max_manifest_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_manifest_bytes must be between 1 KiB and 16 MiB")
        self._max_manifest_bytes = max_manifest_bytes
        self._validator = ModelSnapshotValidator(validation_policy)

    def admit(self, manifest_id: str) -> VerifiedModelSnapshot:
        normalized = str(manifest_id or "").strip()
        if not _MANIFEST_ID_RE.fullmatch(normalized):
            raise ModelManifestValidationError("invalid_identifier", "invalid manifest identifier")
        manifest = self._load_manifest(normalized)
        snapshot_path = self._snapshot_root / manifest.digest
        return self._validator.validate(snapshot_path, manifest)

    def capability_catalog(self) -> list[dict[str, object]]:
        capabilities: list[dict[str, object]] = []
        for path in sorted(self._manifest_root.glob("*.json"))[:256]:
            manifest_id = path.stem
            if not _MANIFEST_ID_RE.fullmatch(manifest_id):
                continue
            try:
                manifest = self._load_manifest(manifest_id)
            except (KeyError, ModelManifestValidationError):
                continue
            status = ModelStatus.READY
            reason_code: str | None = None
            try:
                self._validator.validate(self._snapshot_root / manifest.digest, manifest)
            except ModelManifestValidationError as exc:
                status = ModelStatus.UNAVAILABLE
                reason_code = exc.reason_code
            metadata_languages = manifest.metadata.get("languages", ())
            languages = (
                tuple(str(item) for item in metadata_languages)
                if isinstance(metadata_languages, (list, tuple))
                else ()
            )
            capabilities.append(
                ModelCapability(
                    id=manifest.model_id,
                    engine=manifest.engine,
                    revision=manifest.revision,
                    tasks=tuple(item.value for item in manifest.operations),
                    languages=languages,
                    device=manifest.device,
                    quantization=manifest.quantization,
                    license=manifest.license_id,
                    status=status,
                    manifest_digest=manifest.digest,
                    extensions={
                        "restricted_inference": {
                            "manifest_id": manifest.manifest_id,
                            "format": manifest.model_format,
                            "dtype": manifest.dtype,
                            "max_batch_size": manifest.max_batch_size,
                            "max_sequence_length": manifest.max_sequence_length,
                            "no_generation": True,
                            "reason_code": reason_code,
                        }
                    },
                ).as_dict()
            )
        return capabilities

    def _load_manifest(self, manifest_id: str) -> RestrictedModelManifest:
        normalized = str(manifest_id or "").strip()
        manifest_path = self._manifest_root / f"{normalized}.json"
        raw = _read_regular_file(manifest_path, self._manifest_root, self._max_manifest_bytes)
        try:
            import json

            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelManifestValidationError("invalid_manifest_json", "manifest is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ModelManifestValidationError("invalid_manifest_json", "manifest must be a JSON object")
        manifest = RestrictedModelManifest.from_dict(payload)
        if manifest.manifest_id != normalized:
            raise ModelManifestValidationError("manifest_mismatch", "manifest identifier does not match filename")
        return manifest


def _safe_directory(path: str | Path, name: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"{name} must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{name} does not exist") from exc
    if not resolved.is_dir():
        raise ValueError(f"{name} must be a directory")
    return resolved


def _read_regular_file(path: Path, root: Path, limit: int) -> bytes:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ModelManifestValidationError("unsafe_manifest_path", "manifest escaped root") from exc
    if len(relative.parts) != 1:
        raise ModelManifestValidationError("unsafe_manifest_path", "manifest must be directly below root")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise KeyError(path.stem) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ModelManifestValidationError("unsafe_manifest_file", "manifest must be a regular file")
        if info.st_size > limit:
            raise ModelManifestValidationError("manifest_too_large", "manifest exceeds size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ModelManifestValidationError("manifest_too_large", "manifest exceeds size limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)
