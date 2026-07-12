"""Secure model manifest and local snapshot verification.

The verifier authenticates bytes and paths only; it never imports an ML
framework and never deserializes model weights.  It is therefore safe to use
for hub-side admission checks as well as in the isolated worker immediately
before adapter construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, cast

from agent.services.restricted_inference_contract import RestrictedInferenceOperation

MANIFEST_SCHEMA_VERSION = "restricted_model_manifest.v1"

ENGINE_HUGGINGFACE = "huggingface-transformers"
ENGINE_ONNX = "onnxruntime"
ENGINE_PYTORCH = "pytorch"
ENGINE_SENTENCE_TRANSFORMERS = "sentence-transformers"

FORMAT_ONNX = "onnx"
FORMAT_SAFETENSORS = "safetensors"

SOURCE_HUGGINGFACE_SNAPSHOT = "huggingface_snapshot"
SOURCE_LOCAL_SNAPSHOT = "local_snapshot"

ROLE_CONFIG = "config"
ROLE_EXTERNAL_DATA = "external_data"
ROLE_LABELS = "labels"
ROLE_TOKENIZER = "tokenizer"
ROLE_WEIGHTS = "weights"

_ALLOWED_ENGINES = frozenset(
    {
        ENGINE_HUGGINGFACE,
        ENGINE_ONNX,
        ENGINE_PYTORCH,
        ENGINE_SENTENCE_TRANSFORMERS,
    }
)
_ALLOWED_FORMATS_BY_ENGINE = {
    ENGINE_HUGGINGFACE: frozenset({FORMAT_SAFETENSORS}),
    ENGINE_ONNX: frozenset({FORMAT_ONNX}),
    ENGINE_PYTORCH: frozenset({FORMAT_SAFETENSORS}),
    ENGINE_SENTENCE_TRANSFORMERS: frozenset({FORMAT_SAFETENSORS}),
}
_ALLOWED_SOURCE_TYPES = frozenset({SOURCE_LOCAL_SNAPSHOT, SOURCE_HUGGINGFACE_SNAPSHOT})
_ALLOWED_ROLES = frozenset({ROLE_CONFIG, ROLE_EXTERNAL_DATA, ROLE_LABELS, ROLE_TOKENIZER, ROLE_WEIGHTS})
_MUTABLE_REVISIONS = frozenset({"head", "latest", "main", "master", "stable", "trunk"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$")
_ENGINE_OPERATIONS = {
    ENGINE_HUGGINGFACE: frozenset(RestrictedInferenceOperation),
    ENGINE_ONNX: frozenset(
        {
            RestrictedInferenceOperation.EMBED,
            RestrictedInferenceOperation.CLASSIFY,
            RestrictedInferenceOperation.RERANK,
            RestrictedInferenceOperation.EXTRACT_FEATURES,
            RestrictedInferenceOperation.RISK_SCORE,
        }
    ),
    ENGINE_PYTORCH: frozenset(
        {
            RestrictedInferenceOperation.EMBED,
            RestrictedInferenceOperation.CLASSIFY,
            RestrictedInferenceOperation.RERANK,
            RestrictedInferenceOperation.EXTRACT_FEATURES,
            RestrictedInferenceOperation.RISK_SCORE,
        }
    ),
    ENGINE_SENTENCE_TRANSFORMERS: frozenset(
        {
            RestrictedInferenceOperation.EMBED,
            RestrictedInferenceOperation.RERANK,
            RestrictedInferenceOperation.EXTRACT_FEATURES,
        }
    ),
}

_SAFE_DATA_SUFFIXES = frozenset(
    {
        ".json",
        ".merges",
        ".model",
        ".spm",
        ".txt",
        ".vocab",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "allow_cpu_fallback",
        "device",
        "dtype",
        "engine",
        "files",
        "format",
        "license_id",
        "manifest_id",
        "max_batch_size",
        "max_sequence_length",
        "metadata",
        "model_id",
        "operations",
        "quantization",
        "ram_bytes",
        "revision",
        "schema_version",
        "source_type",
        "tokenizer",
        "trust_remote_code",
        "vram_bytes",
    }
)
_MANIFEST_FILE_FIELDS = frozenset({"path", "relative_path", "role", "sha256", "size_bytes"})


class ModelManifestValidationError(ValueError):
    def __init__(self, reason_code: str, message: str, *, relative_path: str = "") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.relative_path = relative_path


def _identifier(name: str, raw: Any) -> str:
    value = str(raw or "").strip()
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ModelManifestValidationError(
            "invalid_identifier",
            f"{name} must be a non-empty, bounded identifier",
        )
    return value


def _relative_file_path(raw: Any) -> str:
    value = str(raw or "").replace("\\", "/").strip()
    path = PurePosixPath(value)
    if not value or value.startswith("/") or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ModelManifestValidationError(
            "unsafe_manifest_path",
            f"manifest path must stay within the snapshot: {value!r}",
            relative_path=value,
        )
    return path.as_posix()


def _json_mapping(raw: Mapping[str, Any] | None) -> Mapping[str, Any]:
    try:
        copied = json.loads(json.dumps(dict(raw or {}), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ModelManifestValidationError("invalid_metadata", "metadata must be finite JSON data") from exc
    return cast(Mapping[str, Any], _deep_freeze_json(copied))


def _deep_freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _deep_freeze_json(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze_json(nested) for nested in value)
    return value


def _deep_thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw_json(nested) for nested in value]
    return value


@dataclass(frozen=True)
class ModelManifestFile:
    relative_path: str
    sha256: str
    size_bytes: int
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _relative_file_path(self.relative_path))
        digest = str(self.sha256 or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ModelManifestValidationError(
                "invalid_sha256",
                f"invalid SHA-256 for {self.relative_path}",
                relative_path=self.relative_path,
            )
        object.__setattr__(self, "sha256", digest)
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise ModelManifestValidationError(
                "invalid_size",
                f"size_bytes must be a non-negative integer for {self.relative_path}",
                relative_path=self.relative_path,
            )
        role = str(self.role or "").strip().lower()
        if role not in _ALLOWED_ROLES:
            raise ModelManifestValidationError(
                "invalid_file_role",
                f"unsupported file role for {self.relative_path}: {role!r}",
                relative_path=self.relative_path,
            )
        object.__setattr__(self, "role", role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelManifestFile":
        unknown = sorted(set(raw) - _MANIFEST_FILE_FIELDS)
        if unknown:
            raise ModelManifestValidationError(
                "unknown_manifest_file_field",
                f"unknown manifest file fields: {unknown}",
            )
        return cls(
            relative_path=str(raw.get("path") or raw.get("relative_path") or ""),
            sha256=str(raw.get("sha256") or ""),
            size_bytes=cast(int, raw.get("size_bytes")),
            role=str(raw.get("role") or ""),
        )


@dataclass(frozen=True)
class RestrictedModelManifest:
    manifest_id: str
    model_id: str
    engine: str
    model_format: str
    revision: str
    source_type: str
    license_id: str
    operations: tuple[RestrictedInferenceOperation, ...]
    files: tuple[ModelManifestFile, ...]
    trust_remote_code: bool = False
    tokenizer: str = ""
    device: str = "cpu"
    dtype: str = "float32"
    quantization: str = "none"
    ram_bytes: int = 0
    vram_bytes: int = 0
    max_batch_size: int = 8
    max_sequence_length: int = 512
    allow_cpu_fallback: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ModelManifestValidationError("unsupported_manifest_version", self.schema_version)
        object.__setattr__(self, "manifest_id", _identifier("manifest_id", self.manifest_id))
        object.__setattr__(self, "model_id", _identifier("model_id", self.model_id))
        engine = str(self.engine or "").strip().lower()
        if engine not in _ALLOWED_ENGINES:
            raise ModelManifestValidationError("unsupported_engine", f"unsupported engine: {engine!r}")
        object.__setattr__(self, "engine", engine)
        model_format = str(self.model_format or "").strip().lower()
        if model_format not in _ALLOWED_FORMATS_BY_ENGINE[engine]:
            raise ModelManifestValidationError(
                "unsafe_model_format",
                f"format {model_format!r} is not allowed for engine {engine!r}",
            )
        object.__setattr__(self, "model_format", model_format)
        revision = str(self.revision or "").strip()
        if not revision or revision.lower() in _MUTABLE_REVISIONS or any(ch.isspace() for ch in revision):
            raise ModelManifestValidationError(
                "unpinned_revision",
                "revision must be immutable and must not be a mutable branch name",
            )
        if len(revision) > 200:
            raise ModelManifestValidationError("unpinned_revision", "revision is too long")
        object.__setattr__(self, "revision", revision)
        source_type = str(self.source_type or "").strip().lower()
        if source_type not in _ALLOWED_SOURCE_TYPES:
            raise ModelManifestValidationError("unsupported_source_type", source_type)
        object.__setattr__(self, "source_type", source_type)
        license_id = str(self.license_id or "").strip()
        if not license_id or len(license_id) > 200:
            raise ModelManifestValidationError("missing_license", "license_id is required and bounded")
        object.__setattr__(self, "license_id", license_id)
        if self.trust_remote_code is not False:
            raise ModelManifestValidationError(
                "remote_code_forbidden",
                "trust_remote_code must remain false for restricted inference",
            )
        operations: list[RestrictedInferenceOperation] = []
        for operation_item in self.operations:
            try:
                operation = (
                    operation_item
                    if isinstance(operation_item, RestrictedInferenceOperation)
                    else RestrictedInferenceOperation(str(operation_item))
                )
            except ValueError as exc:
                raise ModelManifestValidationError("unsupported_operation", str(operation_item)) from exc
            operations.append(operation)
        if not operations or len(set(operations)) != len(operations):
            raise ModelManifestValidationError("invalid_operations", "operations must be non-empty and unique")
        unsupported = set(operations) - _ENGINE_OPERATIONS[engine]
        if unsupported:
            raise ModelManifestValidationError(
                "unsupported_operation",
                f"engine {engine!r} does not support: {sorted(item.value for item in unsupported)}",
            )
        if engine == ENGINE_SENTENCE_TRANSFORMERS:
            operation_set = set(operations)
            bi_encoder_ops = {
                RestrictedInferenceOperation.EMBED,
                RestrictedInferenceOperation.EXTRACT_FEATURES,
            }
            if operation_set != {RestrictedInferenceOperation.RERANK} and not operation_set.issubset(bi_encoder_ops):
                raise ModelManifestValidationError(
                    "mixed_sentence_transformer_modes",
                    "bi-encoder and CrossEncoder operations require separate manifests",
                )
        object.__setattr__(self, "operations", tuple(operations))
        files = tuple(self.files)
        if not files:
            raise ModelManifestValidationError("missing_files", "manifest must contain at least one file")
        paths = [item.relative_path for item in files]
        if len(paths) != len(set(paths)):
            raise ModelManifestValidationError("duplicate_file", "manifest file paths must be unique")
        if not any(item.role == ROLE_WEIGHTS for item in files):
            raise ModelManifestValidationError("missing_weights", "manifest must declare at least one weights file")
        for manifest_file in files:
            _validate_file_format(manifest_file, model_format)
        object.__setattr__(self, "files", files)
        tokenizer = str(self.tokenizer or "").replace("\\", "/").strip()
        if tokenizer:
            tokenizer = _relative_file_path(tokenizer)
            declared = {item.relative_path: item for item in files}
            if tokenizer not in declared or declared[tokenizer].role not in {ROLE_CONFIG, ROLE_TOKENIZER}:
                raise ModelManifestValidationError(
                    "invalid_tokenizer",
                    "tokenizer must reference a declared tokenizer/config file",
                    relative_path=tokenizer,
                )
        object.__setattr__(self, "tokenizer", tokenizer)
        device = str(self.device or "cpu").strip().lower()
        if device != "cpu" and device != "mps" and not re.fullmatch(r"cuda(?::[0-9]{1,3})?", device):
            raise ModelManifestValidationError("unsupported_device", f"unsupported device: {device!r}")
        object.__setattr__(self, "device", device)
        dtype = str(self.dtype or "float32").strip().lower()
        if dtype not in {"float32", "float16", "bfloat16", "int8", "int4"}:
            raise ModelManifestValidationError("unsupported_dtype", f"unsupported dtype: {dtype!r}")
        object.__setattr__(self, "dtype", dtype)
        quantization = str(self.quantization or "none").strip().lower()
        if quantization not in {"none", "dynamic_int8", "bitsandbytes_int8", "bitsandbytes_int4"}:
            raise ModelManifestValidationError(
                "unsupported_quantization",
                f"unsupported quantization: {quantization!r}",
            )
        if dtype in {"int8", "int4"} and quantization == "none":
            raise ModelManifestValidationError(
                "invalid_quantization",
                "integer dtypes require an explicit quantization mode",
            )
        object.__setattr__(self, "quantization", quantization)
        for name in ("ram_bytes", "vram_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ModelManifestValidationError("invalid_resource_requirement", f"{name} must be non-negative")
        for name, maximum in (("max_batch_size", 1024), ("max_sequence_length", 1_048_576)):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ModelManifestValidationError(
                    "invalid_runtime_limit",
                    f"{name} must be between 1 and {maximum}",
                )
        if not isinstance(self.allow_cpu_fallback, bool):
            raise ModelManifestValidationError("invalid_cpu_fallback", "allow_cpu_fallback must be boolean")
        object.__setattr__(self, "metadata", _json_mapping(self.metadata))

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def declared_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "model_id": self.model_id,
            "engine": self.engine,
            "format": self.model_format,
            "revision": self.revision,
            "source_type": self.source_type,
            "license_id": self.license_id,
            "operations": [item.value for item in self.operations],
            "files": [item.to_dict() for item in self.files],
            "trust_remote_code": False,
            "tokenizer": self.tokenizer,
            "device": self.device,
            "dtype": self.dtype,
            "quantization": self.quantization,
            "ram_bytes": self.ram_bytes,
            "vram_bytes": self.vram_bytes,
            "max_batch_size": self.max_batch_size,
            "max_sequence_length": self.max_sequence_length,
            "allow_cpu_fallback": self.allow_cpu_fallback,
            "metadata": _deep_thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RestrictedModelManifest":
        unknown = sorted(set(raw) - _MANIFEST_FIELDS)
        if unknown:
            raise ModelManifestValidationError(
                "unknown_manifest_field",
                f"unknown manifest fields: {unknown}",
            )
        raw_files = raw.get("files")
        if not isinstance(raw_files, list):
            raise ModelManifestValidationError("missing_files", "files must be a list")
        if not all(isinstance(item, Mapping) for item in raw_files):
            raise ModelManifestValidationError("invalid_files", "every files entry must be an object")
        raw_operations = raw.get("operations")
        if not isinstance(raw_operations, list):
            raise ModelManifestValidationError("invalid_operations", "operations must be a list")
        return cls(
            schema_version=str(raw.get("schema_version") or ""),
            manifest_id=str(raw.get("manifest_id") or ""),
            model_id=str(raw.get("model_id") or ""),
            engine=str(raw.get("engine") or ""),
            model_format=str(raw.get("format") or raw.get("model_format") or ""),
            revision=str(raw.get("revision") or ""),
            source_type=str(raw.get("source_type") or ""),
            license_id=str(raw.get("license_id") or ""),
            operations=tuple(raw_operations),
            files=tuple(ModelManifestFile.from_dict(item) for item in raw_files),
            trust_remote_code=raw.get("trust_remote_code", False),
            tokenizer=str(raw.get("tokenizer") or ""),
            device=str(raw.get("device") or "cpu"),
            dtype=str(raw.get("dtype") or "float32"),
            quantization=str(raw.get("quantization") or "none"),
            ram_bytes=raw.get("ram_bytes", 0),
            vram_bytes=raw.get("vram_bytes", 0),
            max_batch_size=raw.get("max_batch_size", 8),
            max_sequence_length=raw.get("max_sequence_length", 512),
            allow_cpu_fallback=raw.get("allow_cpu_fallback", False),
            metadata=dict(raw.get("metadata") or {}),
        )


def _validate_file_format(item: ModelManifestFile, model_format: str) -> None:
    suffix = PurePosixPath(item.relative_path).suffix.lower()
    if item.role == ROLE_WEIGHTS:
        expected = ".onnx" if model_format == FORMAT_ONNX else ".safetensors"
        if suffix != expected:
            raise ModelManifestValidationError(
                "unsafe_model_format",
                f"weights file must use {expected}: {item.relative_path}",
                relative_path=item.relative_path,
            )
    elif item.role == ROLE_EXTERNAL_DATA:
        if model_format != FORMAT_ONNX or suffix not in {".data", ".bin"}:
            raise ModelManifestValidationError(
                "unsafe_model_format",
                f"external data is only valid for ONNX snapshots: {item.relative_path}",
                relative_path=item.relative_path,
            )
    elif suffix not in _SAFE_DATA_SUFFIXES:
        raise ModelManifestValidationError(
            "unsafe_auxiliary_format",
            f"unsafe auxiliary file extension: {item.relative_path}",
            relative_path=item.relative_path,
        )


@dataclass(frozen=True)
class SnapshotValidationPolicy:
    max_file_bytes: int = 8 * 1024 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024 * 1024
    reject_unlisted_files: bool = True
    allowed_licenses: frozenset[str] = frozenset({"Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MIT", "MPL-2.0"})

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0 or self.max_total_bytes <= 0:
            raise ValueError("snapshot size limits must be positive")
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("max_file_bytes must not exceed max_total_bytes")
        licenses = frozenset(str(item).strip() for item in self.allowed_licenses if str(item).strip())
        if not licenses:
            raise ValueError("allowed_licenses must not be empty")
        object.__setattr__(self, "allowed_licenses", licenses)


@dataclass(frozen=True)
class VerifiedModelSnapshot:
    root: Path
    manifest_id: str
    manifest_digest: str
    model_id: str
    engine: str
    total_size_bytes: int
    file_digests: Mapping[str, str]
    manifest: RestrictedModelManifest | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_digests", MappingProxyType(dict(self.file_digests)))


class ModelSnapshotValidator:
    """Verify a complete local snapshot without parsing its contents."""

    def __init__(self, policy: SnapshotValidationPolicy | None = None) -> None:
        self._policy = policy or SnapshotValidationPolicy()

    def validate(self, snapshot_root: str | Path, manifest: RestrictedModelManifest) -> VerifiedModelSnapshot:
        if manifest.license_id not in self._policy.allowed_licenses:
            raise ModelManifestValidationError(
                "license_not_allowed",
                "model license is not in the configured allowlist",
            )
        root_input = Path(snapshot_root)
        if root_input.is_symlink():
            raise ModelManifestValidationError("snapshot_symlink", "snapshot root must not be a symlink")
        try:
            root = root_input.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ModelManifestValidationError("snapshot_missing", "snapshot root does not exist") from exc
        if not root.is_dir():
            raise ModelManifestValidationError("snapshot_not_directory", "snapshot root must be a directory")
        if manifest.declared_size_bytes > self._policy.max_total_bytes:
            raise ModelManifestValidationError(
                "snapshot_too_large",
                "declared snapshot size exceeds the configured limit",
            )

        expected_paths = {item.relative_path for item in manifest.files}
        if self._policy.reject_unlisted_files:
            self._reject_unlisted_entries(root, expected_paths)

        verified: dict[str, str] = {}
        total_size = 0
        for item in manifest.files:
            if item.size_bytes > self._policy.max_file_bytes:
                raise ModelManifestValidationError(
                    "file_too_large",
                    f"declared file size exceeds the configured limit: {item.relative_path}",
                    relative_path=item.relative_path,
                )
            candidate = self._resolve_regular_file(root, item.relative_path)
            actual_size, actual_digest = self._hash_file(candidate, item.relative_path)
            if actual_size != item.size_bytes:
                raise ModelManifestValidationError(
                    "size_mismatch",
                    f"size mismatch for {item.relative_path}",
                    relative_path=item.relative_path,
                )
            if actual_digest != item.sha256:
                raise ModelManifestValidationError(
                    "hash_mismatch",
                    f"SHA-256 mismatch for {item.relative_path}",
                    relative_path=item.relative_path,
                )
            total_size += actual_size
            if total_size > self._policy.max_total_bytes:
                raise ModelManifestValidationError("snapshot_too_large", "snapshot exceeds the configured limit")
            verified[item.relative_path] = actual_digest
        return VerifiedModelSnapshot(
            root=root,
            manifest_id=manifest.manifest_id,
            manifest_digest=manifest.digest,
            model_id=manifest.model_id,
            engine=manifest.engine,
            total_size_bytes=total_size,
            file_digests=verified,
            manifest=manifest,
        )

    @staticmethod
    def _resolve_regular_file(root: Path, relative_path: str) -> Path:
        current = root
        for part in PurePosixPath(relative_path).parts:
            current = current / part
            try:
                entry_stat = current.lstat()
                mode = entry_stat.st_mode
            except OSError as exc:
                raise ModelManifestValidationError(
                    "snapshot_file_missing",
                    f"snapshot file is missing: {relative_path}",
                    relative_path=relative_path,
                ) from exc
            if stat.S_ISLNK(mode):
                raise ModelManifestValidationError(
                    "snapshot_symlink",
                    f"snapshot contains a symlink: {relative_path}",
                    relative_path=relative_path,
                )
            if current == root / relative_path and entry_stat.st_nlink != 1:
                raise ModelManifestValidationError(
                    "snapshot_hardlink",
                    f"snapshot file must not be a hard link: {relative_path}",
                    relative_path=relative_path,
                )
        try:
            resolved = current.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ModelManifestValidationError(
                "snapshot_file_missing",
                f"snapshot file is missing: {relative_path}",
                relative_path=relative_path,
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ModelManifestValidationError(
                "unsafe_snapshot_path",
                f"snapshot path is not a regular in-root file: {relative_path}",
                relative_path=relative_path,
            )
        return resolved

    @staticmethod
    def _hash_file(path: Path, relative_path: str) -> tuple[int, str]:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ModelManifestValidationError(
                "snapshot_file_unreadable",
                f"cannot open snapshot file: {relative_path}",
                relative_path=relative_path,
            ) from exc
        digest = hashlib.sha256()
        size = 0
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ModelManifestValidationError(
                    "unsafe_snapshot_path",
                    f"snapshot entry is not a regular file: {relative_path}",
                    relative_path=relative_path,
                )
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        finally:
            os.close(descriptor)
        return size, digest.hexdigest()

    @staticmethod
    def _reject_unlisted_entries(root: Path, expected_paths: set[str]) -> None:
        for entry in root.rglob("*"):
            relative = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                raise ModelManifestValidationError(
                    "snapshot_symlink",
                    f"snapshot contains a symlink: {relative}",
                    relative_path=relative,
                )
            if entry.is_dir():
                continue
            if not entry.is_file():
                raise ModelManifestValidationError(
                    "unsafe_snapshot_entry",
                    f"snapshot contains a non-regular entry: {relative}",
                    relative_path=relative,
                )
            if relative not in expected_paths:
                raise ModelManifestValidationError(
                    "unlisted_snapshot_file",
                    f"snapshot contains an unlisted file: {relative}",
                    relative_path=relative,
                )
