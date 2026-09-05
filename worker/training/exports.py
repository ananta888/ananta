"""Atomic worker-side export operations for trained Unsloth models."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class ExportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExportFormat(str, Enum):
    ADAPTER = "adapter"
    MERGED_16BIT = "merged_16bit"
    GGUF = "gguf"


class ExportableModel(Protocol):
    def save_pretrained(self, path: str) -> None: ...

    def save_pretrained_merged(
        self,
        path: str,
        tokenizer: object,
        *,
        save_method: str,
    ) -> None: ...

    def save_pretrained_gguf(
        self,
        path: str,
        tokenizer: object,
        *,
        quantization_method: str,
    ) -> None: ...


class ExportableTokenizer(Protocol):
    def save_pretrained(self, path: str) -> None: ...


@dataclass(frozen=True)
class ExportRequest:
    tenant_id: str
    job_id: str
    attempt_id: str
    dataset_hash: str
    base_model_hash: str
    destination: str
    format: ExportFormat
    quantization_method: str | None = None


@dataclass(frozen=True)
class ExportResult:
    destination: str
    format: str
    artifact_sha256: str
    file_count: int
    total_bytes: int


class UnslothExportExecutor:
    """Exports to a staging directory and publishes with one atomic rename."""

    _GGUF_METHODS = frozenset({"q4_k_m", "q5_k_m", "q8_0"})

    def __init__(self, *, artifact_root: Path) -> None:
        self._root = artifact_root.resolve()

    def execute(
        self,
        *,
        model: ExportableModel,
        tokenizer: ExportableTokenizer,
        request: ExportRequest,
    ) -> ExportResult:
        destination = self._validate(request)
        staging = self._root / f".{destination.name}.{uuid4().hex}.tmp"
        if destination.exists():
            raise ExportError(
                "export_destination_exists",
                "The immutable export destination already exists.",
            )
        staging.mkdir(parents=False, exist_ok=False)
        gguf_staging = staging.with_name(f"{staging.name}_gguf")
        try:
            self._write_export(model, tokenizer, request, staging)
            if request.format is ExportFormat.GGUF:
                self._normalize_gguf_export(staging, gguf_staging)
            digest, file_count, total_bytes = self._digest_tree(staging)
            manifest = {
                "schema_version": 1,
                "tenant_id": request.tenant_id,
                "job_id": request.job_id,
                "attempt_id": request.attempt_id,
                "dataset_hash": request.dataset_hash,
                "base_model_hash": request.base_model_hash,
                "format": request.format.value,
                "quantization_method": request.quantization_method,
                "artifact_sha256": digest,
                "file_count": file_count,
                "total_bytes": total_bytes,
            }
            (staging / "ananta-export-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(staging, destination)
            return ExportResult(
                destination=str(destination.relative_to(self._root)),
                format=request.format.value,
                artifact_sha256=digest,
                file_count=file_count,
                total_bytes=total_bytes,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(gguf_staging, ignore_errors=True)
            raise

    def _validate(self, request: ExportRequest) -> Path:
        if not all(
            (
                request.tenant_id,
                request.job_id,
                request.attempt_id,
                request.destination,
            )
        ):
            raise ExportError(
                "export_scope_missing",
                "Tenant, job, attempt, and destination are required.",
            )
        for value in (request.dataset_hash, request.base_model_hash):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ExportError(
                    "export_provenance_hash_invalid",
                    "Dataset and base-model hashes must be lowercase SHA-256.",
                )
        relative = Path(request.destination)
        if relative.is_absolute() or ".." in relative.parts or relative.name == "":
            raise ExportError(
                "export_destination_invalid",
                "The export destination must be a contained relative path.",
            )
        destination = (self._root / relative).resolve()
        try:
            destination.relative_to(self._root)
        except ValueError as exc:
            raise ExportError(
                "export_destination_escape",
                "The export destination escapes the artifact root.",
            ) from exc
        if destination.parent != self._root:
            raise ExportError(
                "export_destination_nested",
                "Exports must use one direct child of the artifact root.",
            )
        if request.format is ExportFormat.GGUF:
            if request.quantization_method not in self._GGUF_METHODS:
                raise ExportError(
                    "export_quantization_unsupported",
                    "The requested GGUF quantization method is unsupported.",
                )
        elif request.quantization_method is not None:
            raise ExportError(
                "export_quantization_unexpected",
                "Quantization is valid only for GGUF exports.",
            )
        return destination

    @staticmethod
    def _write_export(
        model: ExportableModel,
        tokenizer: ExportableTokenizer,
        request: ExportRequest,
        staging: Path,
    ) -> None:
        if request.format is ExportFormat.ADAPTER:
            model.save_pretrained(str(staging))
            tokenizer.save_pretrained(str(staging))
            return
        if request.format is ExportFormat.MERGED_16BIT:
            model.save_pretrained_merged(
                str(staging),
                tokenizer,
                save_method="merged_16bit",
            )
            return
        model.save_pretrained_gguf(
            str(staging),
            tokenizer,
            quantization_method=request.quantization_method or "",
        )

    @staticmethod
    def _normalize_gguf_export(staging: Path, gguf_staging: Path) -> None:
        """Publish only GGUF output despite Unsloth's sibling-directory API."""
        for root in (staging, gguf_staging):
            if root.is_symlink():
                raise ExportError("export_symlink_forbidden", "GGUF export directories cannot be symlinks.")
            if not root.exists():
                continue
            if any(candidate.is_symlink() for candidate in root.rglob("*")):
                raise ExportError("export_symlink_forbidden", "GGUF exports cannot contain symlinks.")

        if gguf_staging.is_dir():
            for source in sorted(gguf_staging.rglob("*")):
                if not source.is_file() or source.suffix.lower() != ".gguf":
                    continue
                relative = source.relative_to(gguf_staging)
                target = staging / relative
                if target.exists():
                    raise ExportError("export_gguf_collision", "GGUF export paths collided during publication.")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
            shutil.rmtree(gguf_staging)

        gguf_files = [
            candidate
            for candidate in staging.rglob("*")
            if candidate.is_file() and candidate.suffix.lower() == ".gguf"
        ]
        if not gguf_files:
            raise ExportError("export_gguf_missing", "Unsloth did not produce a GGUF model file.")
        for candidate in sorted(staging.rglob("*"), reverse=True):
            if candidate.is_file() and candidate.suffix.lower() != ".gguf":
                candidate.unlink()
            elif candidate.is_dir() and not any(candidate.iterdir()):
                candidate.rmdir()

    @staticmethod
    def _digest_tree(root: Path) -> tuple[str, int, int]:
        digest = hashlib.sha256()
        file_count = 0
        total_bytes = 0
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
                    total_bytes += len(chunk)
            digest.update(b"\0")
            file_count += 1
        if file_count == 0:
            raise ExportError(
                "export_empty",
                "The export backend did not produce any files.",
            )
        return digest.hexdigest(), file_count, total_bytes
