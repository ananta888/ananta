"""Worker-side materialization of immutable model sources."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Protocol
from uuid import uuid4


class ModelImportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelFileMetadata:
    path: str
    size: int


class SnapshotDownloadPort(Protocol):
    def list_files(
        self,
        *,
        model_id: str,
        revision: str,
    ) -> Iterable[ModelFileMetadata]: ...

    def download(
        self,
        *,
        model_id: str,
        revision: str,
        destination: Path,
        allow_patterns: tuple[str, ...],
    ) -> None: ...


@dataclass(frozen=True)
class ModelImportCommand:
    tenant_id: str
    project_id: str
    source_id: str
    kind: str
    expected_sha256: str
    artifact_id: str | None
    model_id: str | None
    revision: str | None
    max_bytes: int
    allow_patterns: tuple[str, ...]
    trust_remote_code: bool
    network_authorized: bool = False
    license_status: str = "pending"
    model_format: str = "transformers"
    architecture: str = "unknown"
    quantization: str | None = None
    capability_facets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelImportResult:
    cache_key: str
    relative_path: str
    content_sha256: str
    file_count: int
    total_bytes: int


class HuggingFaceSnapshotDownloadAdapter:
    """Pinned public snapshot adapter with a metadata size preflight."""

    def __init__(self, *, endpoint: str = "https://huggingface.co") -> None:
        if endpoint != "https://huggingface.co":
            raise ModelImportError(
                "model_download_endpoint_forbidden",
                "Only the approved Hugging Face endpoint is supported.",
            )
        self._endpoint = endpoint

    def list_files(
        self,
        *,
        model_id: str,
        revision: str,
    ) -> Iterable[ModelFileMetadata]:
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            raise ModelImportError(
                "model_download_dependency_missing",
                "huggingface_hub is unavailable in this worker image.",
            ) from exc
        info = HfApi(endpoint=self._endpoint).model_info(
            repo_id=model_id,
            revision=revision,
            files_metadata=True,
            token=False,
        )
        for sibling in info.siblings or ():
            size = getattr(sibling, "size", None)
            path = getattr(sibling, "rfilename", None)
            if not isinstance(path, str) or not isinstance(size, int):
                raise ModelImportError(
                    "model_download_metadata_incomplete",
                    "The remote snapshot lacks authoritative file sizes.",
                )
            yield ModelFileMetadata(path=path, size=size)

    def download(
        self,
        *,
        model_id: str,
        revision: str,
        destination: Path,
        allow_patterns: tuple[str, ...],
    ) -> None:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise ModelImportError(
                "model_download_dependency_missing",
                "huggingface_hub is unavailable in this worker image.",
            ) from exc
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            local_dir=str(destination),
            allow_patterns=list(allow_patterns) or None,
            token=False,
            endpoint=self._endpoint,
        )


class ImmutableModelImportExecutor:
    """Materialize an admitted model inside a worker-owned immutable cache."""

    _IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
    _MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
    _REVISION = re.compile(r"^[0-9a-f]{40,64}$")
    _SHA256 = re.compile(r"^[0-9a-f]{64}$")

    def __init__(
        self,
        *,
        cache_root: Path,
        artifact_root: Path,
        downloads: SnapshotDownloadPort,
        network_enabled: bool = False,
    ) -> None:
        self._cache_root = cache_root.resolve()
        self._artifact_root = artifact_root.resolve()
        self._downloads = downloads
        self._network_enabled = bool(network_enabled)

    def execute(self, command: ModelImportCommand) -> ModelImportResult:
        self._validate(command)
        cache_key = hashlib.sha256(
            (
                f"{command.tenant_id}\0{command.project_id}\0"
                f"{command.source_id}\0{command.expected_sha256}"
            ).encode()
        ).hexdigest()
        destination = self._cache_root / cache_key
        if destination.exists():
            digest, count, size = self._digest_tree(
                destination,
                max_bytes=command.max_bytes,
            )
            if digest != command.expected_sha256:
                raise ModelImportError(
                    "model_cache_hash_mismatch",
                    "An existing immutable cache entry has the wrong digest.",
                )
            self._make_read_only(destination)
            return self._result(cache_key, destination, digest, count, size)

        self._cache_root.mkdir(parents=True, exist_ok=True)
        staging = self._cache_root / f".{cache_key}.{uuid4().hex}.tmp"
        staging.mkdir(mode=0o700)
        try:
            if command.kind == "local_artifact":
                self._copy_local_artifact(command.artifact_id or "", staging)
            else:
                self._download_snapshot(command, staging)
            digest, count, size = self._digest_tree(
                staging,
                max_bytes=command.max_bytes,
            )
            if digest != command.expected_sha256:
                raise ModelImportError(
                    "model_import_hash_mismatch",
                    "The materialized model does not match its expected digest.",
                )
            try:
                os.replace(staging, destination)
            except FileExistsError:
                existing_digest, count, size = self._digest_tree(
                    destination,
                    max_bytes=command.max_bytes,
                )
                if existing_digest != digest:
                    raise ModelImportError(
                        "model_cache_publish_conflict",
                        "A conflicting immutable cache entry was published.",
                    )
            self._make_read_only(destination)
            return self._result(cache_key, destination, digest, count, size)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _copy_local_artifact(self, artifact_id: str, destination: Path) -> None:
        source = (self._artifact_root / artifact_id).resolve()
        try:
            source.relative_to(self._artifact_root)
        except ValueError as exc:
            raise ModelImportError(
                "model_artifact_escape",
                "The local artifact escapes the worker artifact root.",
            ) from exc
        if not source.is_dir() or source.is_symlink():
            raise ModelImportError(
                "model_artifact_unavailable",
                "The local model artifact is unavailable or unsafe.",
            )
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            target = destination / relative
            if item.is_symlink():
                raise ModelImportError(
                    "model_artifact_symlink_forbidden",
                    "Model artifacts must not contain symbolic links.",
                )
            if item.is_dir():
                target.mkdir(exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
            else:
                raise ModelImportError(
                    "model_artifact_type_forbidden",
                    "Model artifacts may contain only files and directories.",
                )

    def _download_snapshot(
        self,
        command: ModelImportCommand,
        destination: Path,
    ) -> None:
        selected_size = 0
        selected_count = 0
        for metadata in self._downloads.list_files(
            model_id=command.model_id or "",
            revision=command.revision or "",
        ):
            if not self._is_allowed(metadata.path, command.allow_patterns):
                continue
            if (
                metadata.size < 0
                or Path(metadata.path).is_absolute()
                or ".." in Path(metadata.path).parts
            ):
                raise ModelImportError(
                    "model_download_metadata_invalid",
                    "Remote file metadata contains an unsafe entry.",
                )
            selected_size += metadata.size
            selected_count += 1
            if selected_size > command.max_bytes:
                raise ModelImportError(
                    "model_download_size_exceeded",
                    "The pinned snapshot exceeds the configured size limit.",
                )
        if selected_count == 0:
            raise ModelImportError(
                "model_download_empty",
                "No remote snapshot files match the allow patterns.",
            )
        self._downloads.download(
            model_id=command.model_id or "",
            revision=command.revision or "",
            destination=destination,
            allow_patterns=command.allow_patterns,
        )

    def _validate(self, command: ModelImportCommand) -> None:
        for value in (
            command.tenant_id,
            command.project_id,
            command.source_id,
        ):
            if not self._IDENTIFIER.fullmatch(value):
                raise ModelImportError(
                    "model_import_scope_invalid",
                    "Tenant, project, and source IDs must be safe identifiers.",
                )
        if not self._SHA256.fullmatch(command.expected_sha256):
            raise ModelImportError(
                "model_import_hash_invalid",
                "A lowercase SHA-256 digest is required.",
            )
        if not 0 < command.max_bytes <= 100 * 1024**3:
            raise ModelImportError(
                "model_import_size_invalid",
                "The model size limit is outside the supported range.",
            )
        if command.trust_remote_code:
            raise ModelImportError(
                "model_import_remote_code_forbidden",
                "Remote model code is disabled.",
            )
        if command.license_status != "approved":
            raise ModelImportError(
                "model_import_license_not_approved",
                "The worker accepts only license-approved model imports.",
            )
        if command.model_format not in {"transformers", "safetensors", "gguf"}:
            raise ModelImportError("model_import_format_invalid", "The model format is unsupported.")
        metadata_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
        if metadata_pattern.fullmatch(command.architecture) is None or (
            command.quantization is not None
            and metadata_pattern.fullmatch(command.quantization) is None
        ):
            raise ModelImportError("model_import_metadata_invalid", "Model metadata is invalid.")
        if (
            len(command.capability_facets) > 64
            or len(set(command.capability_facets)) != len(command.capability_facets)
            or any(
                re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", facet) is None
                for facet in command.capability_facets
            )
        ):
            raise ModelImportError(
                "model_import_capability_facets_invalid",
                "Capability facets must be unique bounded identifiers.",
            )
        if command.kind == "local_artifact":
            if (
                not command.artifact_id
                or not self._IDENTIFIER.fullmatch(command.artifact_id)
                or command.model_id
                or command.revision
                or command.network_authorized
            ):
                raise ModelImportError(
                    "model_import_local_descriptor_invalid",
                    "Local imports require only a safe artifact ID.",
                )
        elif command.kind == "huggingface_snapshot":
            if (
                command.artifact_id
                or not command.model_id
                or not self._MODEL_ID.fullmatch(command.model_id)
                or not command.revision
                or not self._REVISION.fullmatch(command.revision)
            ):
                raise ModelImportError(
                    "model_import_snapshot_descriptor_invalid",
                    "Downloads require a model ID and immutable revision.",
                )
            if not command.network_authorized or not self._network_enabled:
                raise ModelImportError(
                    "model_import_network_not_authorized",
                    "Snapshot downloads require request and worker network authorization.",
                )
        else:
            raise ModelImportError(
                "model_import_kind_unsupported",
                "The requested model source kind is unsupported.",
            )
        for pattern in command.allow_patterns:
            if (
                not pattern
                or Path(pattern).is_absolute()
                or ".." in Path(pattern).parts
            ):
                raise ModelImportError(
                    "model_import_pattern_invalid",
                    "Allow patterns must be safe relative paths.",
                )

    @staticmethod
    def _is_allowed(path: str, patterns: tuple[str, ...]) -> bool:
        return not patterns or any(fnmatch(path, pattern) for pattern in patterns)

    @staticmethod
    def _digest_tree(root: Path, *, max_bytes: int) -> tuple[str, int, int]:
        digest = hashlib.sha256()
        count = 0
        total = 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ModelImportError(
                    "model_import_symlink_forbidden",
                    "Materialized models must not contain symbolic links.",
                )
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ModelImportError(
                            "model_import_size_exceeded",
                            "The materialized model exceeds its size limit.",
                        )
                    digest.update(chunk)
            digest.update(b"\0")
            count += 1
        if count == 0:
            raise ModelImportError(
                "model_import_empty",
                "The materialized model contains no files.",
            )
        return digest.hexdigest(), count, total

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for path in sorted(root.rglob("*"), reverse=True):
            path.chmod(0o500 if path.is_dir() else 0o400)
        root.chmod(0o500)

    def _result(
        self,
        cache_key: str,
        destination: Path,
        digest: str,
        count: int,
        size: int,
    ) -> ModelImportResult:
        return ModelImportResult(
            cache_key=cache_key,
            relative_path=str(destination.relative_to(self._cache_root)),
            content_sha256=digest,
            file_count=count,
            total_bytes=size,
        )


# Compatibility name retained for already shipped Unsloth integrations.
UnslothModelImportExecutor = ImmutableModelImportExecutor
