"""Secure, offline admission for model-analysis snapshots.

The service composes the restricted-inference snapshot validator instead of
duplicating its manifest, digest, path, and license checks.  It adds the
analysis-specific file-count, archive, executable-code, and sparse-file
policies before an admitted snapshot may cross the Hub/worker boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Mapping

from agent.services.restricted_inference_model_manifest import (
    ROLE_EXTERNAL_DATA,
    ModelManifestValidationError,
    ModelSnapshotValidator,
    RestrictedModelManifest,
    SnapshotValidationPolicy,
    VerifiedModelSnapshot,
)


_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tgz",
    ".txz",
    ".xz",
    ".zip",
)
_PICKLE_SUFFIXES = (".ckpt", ".joblib", ".pickle", ".pkl", ".pt", ".pth")
_EXECUTABLE_SUFFIXES = (
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".dylib",
    ".exe",
    ".js",
    ".mjs",
    ".ps1",
    ".py",
    ".pyc",
    ".pyd",
    ".sh",
    ".so",
)


class AnalysisSnapshotAdmissionError(ValueError):
    """A stable, sanitised model-analysis admission failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        relative_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.relative_path = relative_path


@dataclass(frozen=True)
class AnalysisSnapshotAdmissionPolicy:
    """Bounded policy applied before any model parser is invoked."""

    max_file_bytes: int = 8 * 1024**3
    max_total_bytes: int = 64 * 1024**3
    max_files: int = 4096
    max_expansion_ratio: float = 8.0
    reject_sparse_files: bool = True
    allowed_licenses: frozenset[str] = frozenset(
        {"Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MIT", "MPL-2.0"}
    )

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0 or self.max_total_bytes <= 0:
            raise ValueError("snapshot byte limits must be positive")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("max_file_bytes must not exceed max_total_bytes")
        if self.max_files <= 0:
            raise ValueError("max_files must be positive")
        if self.max_expansion_ratio < 1.0:
            raise ValueError("max_expansion_ratio must be at least 1.0")
        licenses = frozenset(
            str(item).strip()
            for item in self.allowed_licenses
            if str(item).strip()
        )
        if not licenses:
            raise ValueError("allowed_licenses must not be empty")
        object.__setattr__(self, "allowed_licenses", licenses)


@dataclass(frozen=True)
class AnalysisSnapshotFile:
    relative_path: str
    sha256: str
    size_bytes: int
    role: str
    suffix: str

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "role": self.role,
            "suffix": self.suffix,
        }


@dataclass(frozen=True)
class AnalysisSnapshotManifest:
    """Tenant-bound external manifest without a container-local path."""

    schema_version: str
    admission_id: str
    snapshot_digest: str
    tenant_id: str
    source_manifest_id: str
    source_manifest_digest: str
    model_id: str
    engine: str
    total_size_bytes: int
    file_count: int
    files: tuple[AnalysisSnapshotFile, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "admission_id": self.admission_id,
            "snapshot_digest": self.snapshot_digest,
            "tenant_id": self.tenant_id,
            "source_manifest_id": self.source_manifest_id,
            "source_manifest_digest": self.source_manifest_digest,
            "model_id": self.model_id,
            "engine": self.engine,
            "total_size_bytes": self.total_size_bytes,
            "file_count": self.file_count,
            "files": [item.to_dict() for item in self.files],
        }


@dataclass(frozen=True)
class AdmittedAnalysisSnapshot:
    """Internal worker result plus its safe cross-container representation."""

    verified_snapshot: VerifiedModelSnapshot
    manifest: AnalysisSnapshotManifest


class ModelAnalysisSnapshotAdmission:
    """Admit a complete local snapshot without executing model-owned code."""

    def __init__(
        self,
        policy: AnalysisSnapshotAdmissionPolicy | None = None,
    ) -> None:
        self._policy = policy or AnalysisSnapshotAdmissionPolicy()
        self._validator = ModelSnapshotValidator(
            SnapshotValidationPolicy(
                max_file_bytes=self._policy.max_file_bytes,
                max_total_bytes=self._policy.max_total_bytes,
                reject_unlisted_files=True,
                allowed_licenses=self._policy.allowed_licenses,
            )
        )

    def admit(
        self,
        *,
        tenant_id: str,
        snapshot_root: str | Path,
        manifest: RestrictedModelManifest,
    ) -> AdmittedAnalysisSnapshot:
        if _TENANT_ID.fullmatch(str(tenant_id or "")) is None:
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_tenant_invalid",
                "A safe tenant identifier is required.",
            )
        if manifest.trust_remote_code:
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_remote_code_forbidden",
                "Remote model code is forbidden for analysis snapshots.",
            )
        if len(manifest.files) > self._policy.max_files:
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_file_count_exceeded",
                "The declared snapshot file count exceeds the configured limit.",
            )

        root = Path(snapshot_root)
        declared_roles = {
            item.relative_path: item.role
            for item in manifest.files
        }
        self._preflight_tree(root, declared_roles)
        try:
            verified = self._validator.validate(root, manifest)
        except ModelManifestValidationError as exc:
            raise AnalysisSnapshotAdmissionError(
                exc.code,
                str(exc),
                relative_path=getattr(exc, "relative_path", None),
            ) from exc

        files = tuple(
            AnalysisSnapshotFile(
                relative_path=item.relative_path,
                sha256=verified.file_digests[item.relative_path],
                size_bytes=item.size_bytes,
                role=item.role,
                suffix=PurePosixPath(item.relative_path).suffix.lower(),
            )
            for item in sorted(
                manifest.files,
                key=lambda candidate: candidate.relative_path,
            )
        )
        content_payload = {
            "schema_version": "model_analysis_snapshot.v1",
            "source_manifest_digest": verified.manifest_digest,
            "files": [item.to_dict() for item in files],
        }
        snapshot_digest = hashlib.sha256(
            json.dumps(
                content_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        admission_id = hashlib.sha256(
            f"{tenant_id}\0{snapshot_digest}".encode("utf-8")
        ).hexdigest()
        external_manifest = AnalysisSnapshotManifest(
            schema_version="model_analysis_snapshot.v1",
            admission_id=admission_id,
            snapshot_digest=snapshot_digest,
            tenant_id=tenant_id,
            source_manifest_id=verified.manifest_id,
            source_manifest_digest=verified.manifest_digest,
            model_id=verified.model_id,
            engine=verified.engine,
            total_size_bytes=verified.total_size_bytes,
            file_count=len(files),
            files=files,
        )
        return AdmittedAnalysisSnapshot(
            verified_snapshot=verified,
            manifest=external_manifest,
        )

    def _preflight_tree(
        self,
        root: Path,
        declared_roles: Mapping[str, str],
    ) -> None:
        if root.is_symlink():
            raise AnalysisSnapshotAdmissionError(
                "snapshot_symlink",
                "Snapshot root must not be a symbolic link.",
            )
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AnalysisSnapshotAdmissionError(
                "snapshot_missing",
                "Snapshot root does not exist.",
            ) from exc
        if not resolved_root.is_dir():
            raise AnalysisSnapshotAdmissionError(
                "snapshot_not_directory",
                "Snapshot root must be a directory.",
            )

        file_count = 0
        total_size = 0
        stack = [resolved_root]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(
                    os.scandir(directory),
                    key=lambda entry: entry.name,
                )
            except OSError as exc:
                raise AnalysisSnapshotAdmissionError(
                    "snapshot_directory_unreadable",
                    "Snapshot directory cannot be read.",
                ) from exc
            for entry in entries:
                candidate = Path(entry.path)
                relative_path = candidate.relative_to(resolved_root).as_posix()
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise AnalysisSnapshotAdmissionError(
                        "snapshot_entry_unreadable",
                        "Snapshot entry cannot be inspected.",
                        relative_path=relative_path,
                    ) from exc
                if stat.S_ISLNK(info.st_mode):
                    raise AnalysisSnapshotAdmissionError(
                        "snapshot_symlink",
                        "Snapshot contains a symbolic link.",
                        relative_path=relative_path,
                    )
                if stat.S_ISDIR(info.st_mode):
                    stack.append(candidate)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise AnalysisSnapshotAdmissionError(
                        "analysis_snapshot_entry_type_forbidden",
                        "Snapshot entries must be regular files or directories.",
                        relative_path=relative_path,
                    )
                if info.st_nlink != 1:
                    raise AnalysisSnapshotAdmissionError(
                        "analysis_snapshot_hardlink_forbidden",
                        "Snapshot files must not be hard links.",
                        relative_path=relative_path,
                    )

                file_count += 1
                total_size += info.st_size
                if file_count > self._policy.max_files:
                    raise AnalysisSnapshotAdmissionError(
                        "analysis_snapshot_file_count_exceeded",
                        "Snapshot file count exceeds the configured limit.",
                    )
                if info.st_size > self._policy.max_file_bytes:
                    raise AnalysisSnapshotAdmissionError(
                        "file_too_large",
                        "Snapshot file exceeds the configured limit.",
                        relative_path=relative_path,
                    )
                if total_size > self._policy.max_total_bytes:
                    raise AnalysisSnapshotAdmissionError(
                        "snapshot_too_large",
                        "Snapshot exceeds the configured total-size limit.",
                    )
                self._reject_unsafe_suffix(
                    relative_path,
                    declared_roles.get(relative_path),
                )
                if self._policy.reject_sparse_files and info.st_size:
                    allocated_bytes = max(
                        int(getattr(info, "st_blocks", 0)) * 512,
                        1,
                    )
                    expansion_ratio = info.st_size / allocated_bytes
                    if expansion_ratio > self._policy.max_expansion_ratio:
                        raise AnalysisSnapshotAdmissionError(
                            "analysis_snapshot_sparse_file_forbidden",
                            "Snapshot contains a sparse or over-expanded file.",
                            relative_path=relative_path,
                        )

    @staticmethod
    def _reject_unsafe_suffix(
        relative_path: str,
        declared_role: str | None,
    ) -> None:
        normalised = relative_path.lower()
        if any(normalised.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_archive_forbidden",
                "Archive containers are forbidden in admitted snapshots.",
                relative_path=relative_path,
            )
        if any(normalised.endswith(suffix) for suffix in _PICKLE_SUFFIXES):
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_pickle_forbidden",
                "Pickle-compatible model formats are forbidden.",
                relative_path=relative_path,
            )
        if any(normalised.endswith(suffix) for suffix in _EXECUTABLE_SUFFIXES):
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_executable_forbidden",
                "Executable model-owned code is forbidden.",
                relative_path=relative_path,
            )
        if normalised.endswith(".bin") and declared_role != ROLE_EXTERNAL_DATA:
            raise AnalysisSnapshotAdmissionError(
                "analysis_snapshot_binary_forbidden",
                "Unclassified binary files are forbidden.",
                relative_path=relative_path,
            )


def immutable_manifest_view(
    admitted: AdmittedAnalysisSnapshot,
) -> Mapping[str, object]:
    """Return a read-only manifest view suitable for an artifact port."""

    return MappingProxyType(admitted.manifest.to_dict())
