"""Security primitives for bounded ml_intern dataset and adapter ingress.

The service owns filesystem containment, streaming upload limits, archive
inspection/extraction and lightweight safetensors validation.  Domain services
receive IDs and streams; only this infrastructure boundary deals with paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterable


class ArtifactSecurityError(ValueError):
    """A fail-closed ingress rejection with a stable machine reason."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ArtifactSecurityPolicy:
    max_file_bytes: int = 100 * 1024 * 1024
    max_request_bytes: int = 110 * 1024 * 1024
    max_tenant_bytes: int = 10 * 1024 * 1024 * 1024
    max_records: int = 100_000
    max_archive_members: int = 256
    max_archive_uncompressed_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_safetensors_header_bytes: int = 16 * 1024 * 1024
    chunk_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_file_bytes,
            self.max_request_bytes,
            self.max_tenant_bytes,
            self.max_records,
            self.max_archive_members,
            self.max_archive_uncompressed_bytes,
            self.max_safetensors_header_bytes,
            self.chunk_bytes,
        )
        if any(value <= 0 for value in integer_limits):
            raise ValueError("artifact security limits must be positive")
        if self.max_request_bytes < self.max_file_bytes:
            raise ValueError("max_request_bytes must be >= max_file_bytes")
        if self.max_tenant_bytes < self.max_file_bytes:
            raise ValueError("max_tenant_bytes must be >= max_file_bytes")
        if not 1.0 <= self.max_compression_ratio <= 100_000.0:
            raise ValueError("max_compression_ratio is out of range")


@dataclass(frozen=True)
class StoredArtifact:
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractedArchive:
    relative_path: str
    archive_kind: str
    member_count: int
    total_uncompressed_bytes: int
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["files"] = list(self.files)
        return payload


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_NESTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".7z",
    ".rar",
    ".gz",
    ".bz2",
    ".xz",
)

_ADAPTER_ALLOWED_FILES = frozenset(
    {
        "adapter_config.json",
        "adapter_model.safetensors",
        "adapter_manifest.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "generation_config.json",
        "vocab.json",
        "vocab.txt",
        "merges.txt",
        "tokenizer.model",
        "chat_template.json",
    }
)
_BLOCKED_MODEL_SUFFIXES = frozenset({".bin", ".pt", ".pth", ".pkl", ".pickle", ".joblib", ".py", ".sh"})
_DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "I64": 8,
    "U64": 8,
    "F64": 8,
}


class MlInternArtifactSecurityService:
    """Fail-closed local artifact store used through bounded domain methods."""

    def __init__(
        self,
        *,
        storage_root: str | Path,
        policy: ArtifactSecurityPolicy | None = None,
    ) -> None:
        root = Path(storage_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        if root.is_symlink():
            raise ArtifactSecurityError("unsafe_storage_root", "storage root must not be a symlink")
        self._root = root.resolve(strict=True)
        self.policy = policy or ArtifactSecurityPolicy()

    @staticmethod
    def tenant_storage_key(tenant_id: str) -> str:
        normalized = str(tenant_id or "").strip()
        if not normalized:
            raise ArtifactSecurityError("tenant_id_required", "tenant_id is required")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_identifier(value: str, *, field_name: str = "id") -> str:
        normalized = str(value or "").strip()
        if not _SAFE_ID.fullmatch(normalized) or normalized in {".", ".."}:
            raise ArtifactSecurityError("invalid_identifier", f"{field_name} has an invalid format")
        return normalized

    def resolve_relative(self, relative_path: str | Path, *, must_exist: bool = False) -> Path:
        raw = str(relative_path or "")
        if not raw or "\x00" in raw or "\\" in raw or _WINDOWS_DRIVE.match(raw):
            raise ArtifactSecurityError("unsafe_path", "artifact path is invalid")
        pure = PurePosixPath(raw)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ArtifactSecurityError("path_escape", "artifact path must be a clean relative path")
        candidate = self._root.joinpath(*pure.parts)
        self._reject_symlink_components(candidate)
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactSecurityError("path_escape", "artifact path escapes storage root") from exc
        if must_exist and not resolved.exists():
            raise ArtifactSecurityError("artifact_not_found", "artifact does not exist")
        return resolved

    def ensure_internal_path(self, path: str | Path, *, must_exist: bool = True) -> Path:
        candidate = Path(path)
        try:
            relative = candidate.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ArtifactSecurityError("path_escape", "path is outside artifact storage") from exc
        return self.resolve_relative(relative.as_posix(), must_exist=must_exist)

    def enforce_record_quota(self, record_count: int) -> None:
        if record_count < 0 or record_count > self.policy.max_records:
            raise ArtifactSecurityError("record_quota_exceeded", "record count exceeds the configured limit")

    def store_upload(
        self,
        stream: BinaryIO,
        *,
        destination_relative: str,
        filename: str,
        media_type: str | None = None,
        allowed_extensions: Iterable[str] | None = None,
        allowed_media_types: Iterable[str] | None = None,
        content_kind: str | None = None,
        declared_size: int | None = None,
        expected_sha256: str | None = None,
        request_bytes_used: int = 0,
        tenant_bytes_used: int = 0,
        overwrite: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> StoredArtifact:
        """Stream one upload to a private temp file and atomically promote it."""

        clean_name = Path(str(filename or "")).name
        if not clean_name or clean_name != str(filename) or "\x00" in clean_name:
            raise ArtifactSecurityError("invalid_filename", "upload filename is invalid")
        extension = _compound_suffix(clean_name)
        if allowed_extensions is not None:
            allowed = {str(item).lower() for item in allowed_extensions}
            if extension not in allowed:
                raise ArtifactSecurityError("extension_not_allowed", "upload extension is not allowed")
        normalized_media = str(media_type or "").split(";", 1)[0].strip().lower() or None
        if allowed_media_types is not None:
            allowed_media = {str(item).lower() for item in allowed_media_types}
            if normalized_media not in allowed_media:
                raise ArtifactSecurityError("media_type_not_allowed", "upload media type is not allowed")
        if declared_size is not None:
            try:
                declared = int(declared_size)
            except (TypeError, ValueError) as exc:
                raise ArtifactSecurityError("invalid_content_length", "declared upload size is invalid") from exc
            if declared < 0 or declared > self.policy.max_file_bytes:
                raise ArtifactSecurityError("file_quota_exceeded", "declared upload size exceeds the file limit")
            if request_bytes_used + declared > self.policy.max_request_bytes:
                raise ArtifactSecurityError("request_quota_exceeded", "declared upload exceeds the request limit")
            if tenant_bytes_used + declared > self.policy.max_tenant_bytes:
                raise ArtifactSecurityError("tenant_quota_exceeded", "declared upload exceeds the tenant limit")

        target = self.resolve_relative(destination_relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._reject_symlink_components(target)
        if target.exists() and not overwrite:
            raise ArtifactSecurityError("artifact_exists", "artifact destination already exists")

        temporary: Path | None = None
        digest = hashlib.sha256()
        total = 0
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".upload-", dir=str(target.parent))
            temporary = Path(temp_name)
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                while True:
                    if cancel_check is not None and cancel_check():
                        raise ArtifactSecurityError("upload_cancelled", "upload was cancelled")
                    chunk = stream.read(self.policy.chunk_bytes)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise ArtifactSecurityError("invalid_upload_stream", "upload stream must yield bytes")
                    total += len(chunk)
                    if total > self.policy.max_file_bytes:
                        raise ArtifactSecurityError("file_quota_exceeded", "upload exceeds the file limit")
                    if request_bytes_used + total > self.policy.max_request_bytes:
                        raise ArtifactSecurityError("request_quota_exceeded", "upload exceeds the request limit")
                    if tenant_bytes_used + total > self.policy.max_tenant_bytes:
                        raise ArtifactSecurityError("tenant_quota_exceeded", "upload exceeds the tenant limit")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if declared_size is not None and total != int(declared_size):
                raise ArtifactSecurityError("size_mismatch", "actual upload size differs from declared size")
            actual_digest = digest.hexdigest()
            if expected_sha256 is not None and actual_digest != str(expected_sha256).lower():
                raise ArtifactSecurityError("hash_mismatch", "upload digest differs from expected digest")
            self._validate_content_kind(temporary, content_kind)
            if target.exists() and not overwrite:
                raise ArtifactSecurityError("artifact_exists", "artifact destination already exists")
            os.replace(temporary, target)
            temporary = None
            return StoredArtifact(
                relative_path=target.relative_to(self._root).as_posix(),
                sha256=actual_digest,
                size_bytes=total,
                media_type=normalized_media,
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def atomic_write_json(self, destination_relative: str, payload: dict[str, Any]) -> None:
        target = self.resolve_relative(destination_relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".json-", dir=str(target.parent))
            temporary = Path(temp_name)
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def extract_archive(
        self,
        archive_path: str | Path,
        *,
        destination_relative: str,
        archive_kind: str | None = None,
    ) -> ExtractedArchive:
        """Validate and extract ZIP/TAR without trusting archive member paths."""

        archive = self.ensure_internal_path(archive_path, must_exist=True)
        if not archive.is_file() or archive.is_symlink():
            raise ArtifactSecurityError("unsafe_archive", "archive must be a regular file")
        kind = self._detect_archive_kind(archive, requested=archive_kind)
        destination = self.resolve_relative(destination_relative)
        if destination.exists():
            raise ArtifactSecurityError("artifact_exists", "archive destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage = Path(tempfile.mkdtemp(prefix=".extract-", dir=str(destination.parent)))
        os.chmod(stage, 0o700)
        try:
            if kind == "zip":
                files, total = self._extract_zip(archive, stage)
            else:
                files, total = self._extract_tar(archive, stage)
            os.replace(stage, destination)
            return ExtractedArchive(
                relative_path=destination.relative_to(self._root).as_posix(),
                archive_kind=kind,
                member_count=len(files),
                total_uncompressed_bytes=total,
                files=tuple(files),
            )
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def validate_adapter_tree(self, adapter_root: str | Path) -> dict[str, Any]:
        """Validate an extracted PEFT adapter without importing model code/weights."""

        root = self.ensure_internal_path(adapter_root, must_exist=True)
        if not root.is_dir() or root.is_symlink():
            raise ArtifactSecurityError("invalid_adapter_tree", "adapter root must be a regular directory")
        files: list[Path] = []
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ArtifactSecurityError("archive_link_forbidden", "adapter tree contains a link")
            if path.is_file():
                relative = path.relative_to(root)
                if len(relative.parts) != 1:
                    raise ArtifactSecurityError("adapter_nested_path", "adapter files must be at the adapter root")
                files.append(path)
        if not files or len(files) > self.policy.max_archive_members:
            raise ArtifactSecurityError("invalid_adapter_file_count", "adapter file count is invalid")

        names = {path.name.lower() for path in files}
        blocked = sorted(name for name in names if Path(name).suffix.lower() in _BLOCKED_MODEL_SUFFIXES)
        if blocked:
            raise ArtifactSecurityError("unsafe_weight_format", "pickle or executable model files are forbidden")
        unknown = sorted(name for name in names if name not in _ADAPTER_ALLOWED_FILES)
        if unknown:
            raise ArtifactSecurityError("adapter_file_not_allowed", "adapter contains files outside the allowlist")
        if "adapter_config.json" not in names or "adapter_model.safetensors" not in names:
            raise ArtifactSecurityError(
                "adapter_required_file_missing",
                "adapter config and safetensors weights are required",
            )

        config_path = next(path for path in files if path.name.lower() == "adapter_config.json")
        if config_path.stat().st_size > min(self.policy.max_file_bytes, 2 * 1024 * 1024):
            raise ArtifactSecurityError("adapter_config_too_large", "adapter config exceeds its size limit")
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactSecurityError("invalid_adapter_config", "adapter config is not valid UTF-8 JSON") from exc
        if not isinstance(config, dict):
            raise ArtifactSecurityError("invalid_adapter_config", "adapter config must be a JSON object")

        weights_path = next(path for path in files if path.name.lower() == "adapter_model.safetensors")
        safetensors = self.inspect_safetensors(weights_path)
        file_rows = []
        for path in sorted(files, key=lambda item: item.name.lower()):
            file_rows.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _hash_file(path, chunk_bytes=self.policy.chunk_bytes),
                }
            )
        return {
            "config": config,
            "files": file_rows,
            "safetensors": safetensors,
            "total_bytes": sum(row["size_bytes"] for row in file_rows),
            "tree_sha256": hashlib.sha256(
                json.dumps(
                    file_rows,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
        }

    def validate_needle_adapter_tree(self, adapter_root: str | Path) -> dict[str, Any]:
        """Validate the opaque Needle LoRA without ever deserializing pickle."""

        root = self.ensure_internal_path(adapter_root, must_exist=True)
        if not root.is_dir() or root.is_symlink():
            raise ArtifactSecurityError("invalid_adapter_tree", "adapter root must be a regular directory")
        children = list(root.iterdir())
        if len(children) != 1:
            raise ArtifactSecurityError(
                "needle_adapter_file_count_invalid",
                "Needle adapter tree must contain only adapter.pkl",
            )
        adapter = children[0]
        if adapter.name != "adapter.pkl" or adapter.is_symlink() or not adapter.is_file():
            raise ArtifactSecurityError(
                "needle_adapter_file_invalid",
                "Needle adapter tree must contain one regular adapter.pkl",
            )
        size = adapter.stat().st_size
        if size < 1 or size > self.policy.max_file_bytes:
            raise ArtifactSecurityError("needle_adapter_size_invalid", "Needle adapter size is invalid")
        rows = [
            {
                "name": adapter.name,
                "size_bytes": size,
                "sha256": _hash_file(adapter, chunk_bytes=self.policy.chunk_bytes),
            }
        ]
        return {
            "files": rows,
            "total_bytes": size,
            "tree_sha256": hashlib.sha256(
                json.dumps(
                    rows,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
        }

    def promote_verified_tree(
        self,
        source_root: str | Path,
        *,
        destination_relative: str,
        expected_files: dict[str, str],
    ) -> str:
        """Copy an already inspected flat tree and atomically make it visible."""

        source = self.ensure_internal_path(source_root, must_exist=True)
        if not source.is_dir() or source.is_symlink():
            raise ArtifactSecurityError("invalid_promotion_source", "promotion source must be a directory")
        destination = self.resolve_relative(destination_relative)
        if destination.exists():
            raise ArtifactSecurityError("artifact_exists", "promotion destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage = Path(tempfile.mkdtemp(prefix=".promote-", dir=str(destination.parent)))
        os.chmod(stage, 0o700)
        try:
            actual_names = {path.name for path in source.iterdir() if path.is_file() and not path.is_symlink()}
            if actual_names != set(expected_files):
                raise ArtifactSecurityError("promotion_manifest_mismatch", "promotion file set differs from manifest")
            for name, expected_digest in sorted(expected_files.items()):
                if Path(name).name != name:
                    raise ArtifactSecurityError("promotion_manifest_mismatch", "promotion filename is invalid")
                source_file = source / name
                target = stage / name
                read_flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
                source_descriptor = os.open(source_file, read_flags)
                target_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                digest = hashlib.sha256()
                total = 0
                try:
                    while True:
                        chunk = os.read(source_descriptor, self.policy.chunk_bytes)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.policy.max_file_bytes:
                            raise ArtifactSecurityError("file_quota_exceeded", "promotion source exceeds file limit")
                        digest.update(chunk)
                        os.write(target_descriptor, chunk)
                    os.fsync(target_descriptor)
                finally:
                    os.close(source_descriptor)
                    os.close(target_descriptor)
                if digest.hexdigest() != str(expected_digest).lower():
                    raise ArtifactSecurityError("hash_mismatch", "promotion source changed after validation")
            os.replace(stage, destination)
            return destination.relative_to(self._root).as_posix()
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def inspect_safetensors(self, path: str | Path) -> dict[str, Any]:
        candidate = self.ensure_internal_path(path, must_exist=True)
        if not candidate.is_file() or candidate.is_symlink():
            raise ArtifactSecurityError("invalid_safetensors", "safetensors input must be a regular file")
        size = candidate.stat().st_size
        if size < 10 or size > self.policy.max_file_bytes:
            raise ArtifactSecurityError("invalid_safetensors", "safetensors file size is invalid")
        with candidate.open("rb") as handle:
            raw_length = handle.read(8)
            header_length = int.from_bytes(raw_length, byteorder="little", signed=False)
            if header_length <= 1 or header_length > self.policy.max_safetensors_header_bytes:
                raise ArtifactSecurityError("invalid_safetensors_header", "safetensors header length is invalid")
            if 8 + header_length > size:
                raise ArtifactSecurityError("invalid_safetensors_header", "safetensors header exceeds file size")
            raw_header = handle.read(header_length)
        try:
            header = json.loads(raw_header.decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeError, json.JSONDecodeError, ArtifactSecurityError) as exc:
            if isinstance(exc, ArtifactSecurityError):
                raise
            raise ArtifactSecurityError("invalid_safetensors_header", "safetensors header is invalid JSON") from exc
        if not isinstance(header, dict):
            raise ArtifactSecurityError("invalid_safetensors_header", "safetensors header must be an object")
        metadata = header.pop("__metadata__", None)
        if metadata is not None and not isinstance(metadata, dict):
            raise ArtifactSecurityError("invalid_safetensors_header", "safetensors metadata must be an object")
        if not header:
            raise ArtifactSecurityError("invalid_safetensors_header", "safetensors contains no tensors")

        data_size = size - 8 - header_length
        ranges: list[tuple[int, int]] = []
        for tensor_name, tensor in header.items():
            if not isinstance(tensor_name, str) or not tensor_name or not isinstance(tensor, dict):
                raise ArtifactSecurityError("invalid_safetensors_tensor", "safetensors tensor entry is invalid")
            dtype = str(tensor.get("dtype") or "")
            shape = tensor.get("shape")
            offsets = tensor.get("data_offsets")
            if (
                dtype not in _DTYPE_BYTES
                or not isinstance(shape, list)
                or not isinstance(offsets, list)
                or len(offsets) != 2
            ):
                raise ArtifactSecurityError("invalid_safetensors_tensor", "safetensors tensor metadata is invalid")
            if any(not isinstance(dimension, int) or dimension < 0 for dimension in shape):
                raise ArtifactSecurityError("invalid_safetensors_tensor", "safetensors tensor shape is invalid")
            start, end = offsets
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > data_size:
                raise ArtifactSecurityError("invalid_safetensors_offsets", "safetensors offsets are invalid")
            elements = 1
            for dimension in shape:
                elements *= dimension
            if end - start != elements * _DTYPE_BYTES[dtype]:
                raise ArtifactSecurityError(
                    "invalid_safetensors_offsets",
                    "safetensors tensor byte size is inconsistent",
                )
            ranges.append((start, end))
        ranges.sort()
        expected_start = 0
        for start, end in ranges:
            if start != expected_start:
                raise ArtifactSecurityError(
                    "invalid_safetensors_offsets",
                    "safetensors data ranges overlap or contain holes",
                )
            expected_start = end
        if expected_start != data_size:
            raise ArtifactSecurityError("invalid_safetensors_offsets", "safetensors data is not fully described")
        return {
            "tensor_count": len(ranges),
            "header_bytes": header_length,
            "data_bytes": data_size,
        }

    def remove_relative_tree(self, relative_path: str) -> None:
        target = self.resolve_relative(relative_path)
        if not target.exists():
            return
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

    def _reject_symlink_components(self, candidate: Path) -> None:
        current = self._root
        try:
            relative = candidate.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactSecurityError("path_escape", "artifact path escapes storage root") from exc
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ArtifactSecurityError("symlink_forbidden", "artifact path contains a symlink")

    def _validate_content_kind(self, path: Path, content_kind: str | None) -> None:
        kind = str(content_kind or "").strip().lower()
        if not kind:
            return
        if kind == "zip" and not zipfile.is_zipfile(path):
            raise ArtifactSecurityError("content_type_mismatch", "upload is not a ZIP archive")
        if kind == "tar" and not tarfile.is_tarfile(path):
            raise ArtifactSecurityError("content_type_mismatch", "upload is not a TAR archive")
        if kind in {"json", "jsonl"}:
            with path.open("rb") as handle:
                prefix = handle.read(min(path.stat().st_size, 8192))
            try:
                text = prefix.decode("utf-8")
            except UnicodeError as exc:
                raise ArtifactSecurityError("content_type_mismatch", "dataset upload is not UTF-8 text") from exc
            first = text.lstrip()[:1]
            allowed = {"{"} if kind == "jsonl" else {"{", "["}
            if first not in allowed:
                raise ArtifactSecurityError("content_type_mismatch", "dataset content does not match its extension")
        if kind == "safetensors":
            self.inspect_safetensors(path)

    def _detect_archive_kind(self, archive: Path, *, requested: str | None) -> str:
        detected = "zip" if zipfile.is_zipfile(archive) else ("tar" if tarfile.is_tarfile(archive) else "")
        expected = str(requested or "").strip().lower()
        if not detected:
            raise ArtifactSecurityError("unsupported_archive", "upload is neither a valid ZIP nor TAR archive")
        if expected and expected != detected:
            raise ArtifactSecurityError("content_type_mismatch", "archive content differs from declared type")
        return detected

    def _extract_zip(self, archive: Path, stage: Path) -> tuple[list[str], int]:
        files: list[str] = []
        total = 0
        seen: set[str] = set()
        with zipfile.ZipFile(archive, "r") as handle:
            members = handle.infolist()
            if len(members) > self.policy.max_archive_members:
                raise ArtifactSecurityError("archive_member_limit", "archive contains too many members")
            for member in members:
                relative = _safe_archive_member(member.filename)
                duplicate_key = relative.casefold()
                if duplicate_key in seen:
                    raise ArtifactSecurityError("archive_duplicate_member", "archive contains duplicate member names")
                seen.add(duplicate_key)
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(unix_mode):
                    raise ArtifactSecurityError("archive_link_forbidden", "archive links are forbidden")
                file_type = stat.S_IFMT(unix_mode)
                if file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}:
                    raise ArtifactSecurityError("archive_special_file", "archive special files are forbidden")
                if member.flag_bits & 0x1:
                    raise ArtifactSecurityError("archive_encrypted", "encrypted archives are not accepted")
                if member.is_dir():
                    (stage / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                _reject_nested_archive(relative)
                self._check_archive_size(member.file_size, member.compress_size, total)
                total += member.file_size
                target = _stage_target(stage, relative)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with handle.open(member, "r") as source:
                    _copy_exact(source, target, expected_size=member.file_size, chunk_bytes=self.policy.chunk_bytes)
                files.append(relative)
        self._check_overall_compression(total, archive.stat().st_size)
        return sorted(files), total

    def _extract_tar(self, archive: Path, stage: Path) -> tuple[list[str], int]:
        files: list[str] = []
        total = 0
        seen: set[str] = set()
        with tarfile.open(archive, mode="r:*") as handle:
            members = handle.getmembers()
            if len(members) > self.policy.max_archive_members:
                raise ArtifactSecurityError("archive_member_limit", "archive contains too many members")
            for member in members:
                relative = _safe_archive_member(member.name)
                duplicate_key = relative.casefold()
                if duplicate_key in seen:
                    raise ArtifactSecurityError("archive_duplicate_member", "archive contains duplicate member names")
                seen.add(duplicate_key)
                if member.issym() or member.islnk():
                    raise ArtifactSecurityError("archive_link_forbidden", "archive links are forbidden")
                if member.isdir():
                    (stage / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isfile():
                    raise ArtifactSecurityError("archive_special_file", "archive special files are forbidden")
                _reject_nested_archive(relative)
                self._check_archive_size(member.size, member.size, total)
                total += member.size
                source = handle.extractfile(member)
                if source is None:
                    raise ArtifactSecurityError("archive_read_failed", "archive member cannot be read")
                target = _stage_target(stage, relative)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source:
                    _copy_exact(source, target, expected_size=member.size, chunk_bytes=self.policy.chunk_bytes)
                files.append(relative)
        self._check_overall_compression(total, archive.stat().st_size)
        return sorted(files), total

    def _check_archive_size(self, size: int, compressed_size: int, current_total: int) -> None:
        if size < 0 or size > self.policy.max_file_bytes:
            raise ArtifactSecurityError("archive_file_limit", "archive member exceeds the file limit")
        if current_total + size > self.policy.max_archive_uncompressed_bytes:
            raise ArtifactSecurityError("archive_size_limit", "archive exceeds the uncompressed size limit")
        if size and (compressed_size <= 0 or size / compressed_size > self.policy.max_compression_ratio):
            raise ArtifactSecurityError("archive_compression_bomb", "archive member compression ratio is unsafe")

    def _check_overall_compression(self, unpacked: int, packed: int) -> None:
        if unpacked and (packed <= 0 or unpacked / packed > self.policy.max_compression_ratio):
            raise ArtifactSecurityError("archive_compression_bomb", "archive compression ratio is unsafe")


def _compound_suffix(filename: str) -> str:
    lowered = filename.lower()
    for suffix in sorted(_NESTED_ARCHIVE_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            return suffix
    return Path(lowered).suffix


def _safe_archive_member(name: str) -> str:
    raw = str(name or "")
    if not raw or "\x00" in raw or "\\" in raw or _WINDOWS_DRIVE.match(raw):
        raise ArtifactSecurityError("archive_path_escape", "archive member path is unsafe")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ArtifactSecurityError("archive_path_escape", "archive member escapes extraction root")
    return pure.as_posix().rstrip("/")


def _reject_nested_archive(relative: str) -> None:
    lowered = relative.lower()
    if any(lowered.endswith(suffix) for suffix in _NESTED_ARCHIVE_SUFFIXES):
        raise ArtifactSecurityError("nested_archive_forbidden", "nested archives are forbidden")


def _stage_target(stage: Path, relative: str) -> Path:
    target = (stage / relative).resolve(strict=False)
    try:
        target.relative_to(stage.resolve(strict=True))
    except ValueError as exc:
        raise ArtifactSecurityError("archive_path_escape", "archive member escapes extraction root") from exc
    if target.exists():
        raise ArtifactSecurityError("archive_duplicate_member", "archive member target already exists")
    return target


def _copy_exact(source: BinaryIO, target: Path, *, expected_size: int, chunk_bytes: int) -> None:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    total = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            while True:
                chunk = source.read(min(chunk_bytes, expected_size + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise ArtifactSecurityError("archive_size_mismatch", "archive member exceeds declared size")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if total != expected_size:
        target.unlink(missing_ok=True)
        raise ArtifactSecurityError("archive_size_mismatch", "archive member is truncated")


def _hash_file(path: Path, *, chunk_bytes: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactSecurityError("duplicate_json_key", "JSON object contains duplicate keys")
        result[key] = value
    return result
