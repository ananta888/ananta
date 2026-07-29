"""Hash-verified deterministic export of registry-backed LoRA adapters."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService


class StorageCatalogPort(Protocol):
    def register(self, **values: Any) -> Any: ...


class AdapterExportError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MlInternAdapterExportService:
    """Verify a registry artifact tree before producing a stable ZIP bundle."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        registry: MlInternAdapterRegistryService,
        storage_catalog: StorageCatalogPort | None = None,
    ) -> None:
        self._root = Path(artifact_root)
        self._security = MlInternArtifactSecurityService(storage_root=self._root)
        self._registry = registry
        self._storage_catalog = storage_catalog

    def export(
        self,
        adapter_id: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> dict[str, Any]:
        adapter_dir, manifest, artifact_id, output = self._prepare_export(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Always replace the deterministic bundle. Reusing an existing path
        # would let out-of-band tampering become the newly announced hash.
        self._write_zip(output, adapter_dir, manifest)
        digest = self._validate_export_bundle(output, artifact_id)
        if (
            self._storage_catalog is not None
            and tenant_id is not None
            and owner_subject is not None
        ):
            relative = output.relative_to(self._root.resolve()).as_posix()
            parts = PurePosixPath(relative).parts
            try:
                self._storage_catalog.register(
                    tenant_id=tenant_id,
                    owner_scope_digest=_tenant_scope_digest(
                        tenant_id,
                        owner_subject,
                    ),
                    artifact_id=artifact_id,
                    kind="export",
                    relative_ref=relative,
                    job_id=parts[3],
                    attempt_id=parts[5],
                    artifact_sha256=digest,
                    size_bytes=output.stat().st_size,
                )
            except Exception:
                output.unlink(missing_ok=True)
                raise
        return {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size_bytes": output.stat().st_size,
            "download_url": f"/api/ml-intern-training/exports/{artifact_id}",
        }

    def preview_export(
        self,
        adapter_id: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> dict[str, Any]:
        """Validate an export without creating or replacing an artifact."""

        _adapter_dir, manifest, artifact_id, _output = self._prepare_export(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        return {
            "artifact_id": artifact_id,
            "adapter_id": str(manifest["adapter_id"]),
            "version": str(manifest["version"]),
            "status": str(manifest["status"]),
            "artifact_sha256": str(manifest["artifact_sha256"]),
        }

    def _prepare_export(
        self,
        adapter_id: str,
        *,
        tenant_id: str | None,
        owner_subject: str | None,
    ) -> tuple[Path, dict[str, Any], str, Path]:
        record = self._registry.get(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        if record is None:
            raise AdapterExportError("adapter_not_found", "adapter does not exist")
        if record.status not in {"evaluated", "approved", "rejected", "deprecated"}:
            raise AdapterExportError("adapter_not_exportable", "adapter must be evaluated before export")
        raw_path = record.artifact_paths.get("adapter_dir") or record.artifact_paths.get("adapter_path")
        if not raw_path:
            raise AdapterExportError("adapter_artifact_missing", "adapter has no registered artifact")
        try:
            adapter_dir = (
                self._security.ensure_internal_path(raw_path, must_exist=True)
                if Path(str(raw_path)).is_absolute()
                else self._security.resolve_relative(str(raw_path), must_exist=True)
            )
            inspected = self._security.validate_adapter_tree(adapter_dir)
        except Exception as exc:
            raise AdapterExportError("adapter_artifact_invalid", "adapter artifact verification failed") from exc
        if not record.artifact_sha256:
            raise AdapterExportError(
                "adapter_hash_unbound",
                "adapter artifact has no immutable registry hash and must be re-imported or re-published",
            )
        if inspected["tree_sha256"] != record.artifact_sha256:
            raise AdapterExportError(
                "adapter_hash_mismatch",
                "adapter artifact no longer matches its immutable registry hash",
            )

        manifest = {
            "schema": "ananta.lora-adapter-export.v1",
            "adapter_id": record.adapter_id,
            "version": record.version,
            "base_model": record.base_model,
            "method": record.method,
            "status": record.status,
            "dataset_hash": record.dataset_hash,
            "config_hash": record.config_hash,
            "artifact_sha256": record.artifact_sha256,
            "files": inspected["files"],
        }
        identity = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        artifact_id = f"lora-export-{identity[:32]}"
        if tenant_id is not None and owner_subject is not None:
            scope_directory = _tenant_scope_digest(
                tenant_id,
                owner_subject,
            )
            relative = (
                f"tenants/{scope_directory}/jobs/{record.adapter_id}/"
                f"attempts/registry-v{record.registry_version}/exports/{artifact_id}.zip"
            )
        else:
            scope_directory = _scope_directory(tenant_id, owner_subject)
            relative = f"exports/{scope_directory}/{artifact_id}.zip"
        output = self._security.resolve_relative(relative)
        return adapter_dir, manifest, artifact_id, output

    def resolve_export(
        self,
        artifact_id: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> tuple[Path, str]:
        if not artifact_id.startswith("lora-export-") or len(artifact_id) != len("lora-export-") + 32:
            raise AdapterExportError("export_not_found", "adapter export does not exist")
        try:
            if tenant_id is not None and owner_subject is not None:
                scope_directory = _tenant_scope_digest(
                    tenant_id,
                    owner_subject,
                )
                matches = tuple(
                    self._root.glob(
                        f"tenants/{scope_directory}/jobs/*/attempts/*/exports/{artifact_id}.zip"
                    )
                )
                if len(matches) != 1:
                    raise AdapterExportError(
                        "export_not_found",
                        "adapter export does not exist",
                    )
                path = self._security.ensure_internal_path(
                    matches[0],
                    must_exist=True,
                )
            else:
                scope_directory = _scope_directory(tenant_id, owner_subject)
                path = self._security.resolve_relative(
                    f"exports/{scope_directory}/{artifact_id}.zip",
                    must_exist=True,
                )
        except Exception as exc:
            raise AdapterExportError("export_not_found", "adapter export does not exist") from exc
        if not path.is_file() or path.is_symlink():
            raise AdapterExportError("export_not_found", "adapter export does not exist")
        return path, self._validate_export_bundle(path, artifact_id)

    def _validate_export_bundle(self, path: Path, artifact_id: str) -> str:
        try:
            if path.stat().st_size > self._security.policy.max_archive_uncompressed_bytes:
                raise AdapterExportError("export_hash_mismatch", "adapter export exceeds its bound")
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(names) != len(set(names)) or "ananta_export_manifest.json" not in names:
                    raise AdapterExportError("export_hash_mismatch", "adapter export entries are invalid")
                if any(
                    info.is_dir()
                    or info.flag_bits & 0x1
                    or not _safe_export_name(info.filename)
                    or info.file_size > self._security.policy.max_file_bytes
                    for info in infos
                ):
                    raise AdapterExportError("export_hash_mismatch", "adapter export entry is unsafe")
                total_size = sum(info.file_size for info in infos)
                if total_size > self._security.policy.max_archive_uncompressed_bytes:
                    raise AdapterExportError("export_hash_mismatch", "adapter export expands beyond its bound")
                manifest_bytes = archive.read("ananta_export_manifest.json")
                if len(manifest_bytes) > 1024 * 1024:
                    raise AdapterExportError("export_hash_mismatch", "adapter export manifest is oversized")
                manifest = json.loads(manifest_bytes)
                if not isinstance(manifest, dict) or manifest.get("schema") != "ananta.lora-adapter-export.v1":
                    raise AdapterExportError("export_hash_mismatch", "adapter export manifest is invalid")
                identity = hashlib.sha256(
                    json.dumps(
                        manifest,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
                if artifact_id != f"lora-export-{identity[:32]}":
                    raise AdapterExportError("export_hash_mismatch", "adapter export identity is invalid")
                raw_files = manifest.get("files")
                if not isinstance(raw_files, list) or not raw_files:
                    raise AdapterExportError("export_hash_mismatch", "adapter export file manifest is invalid")
                expected: dict[str, tuple[int, str]] = {}
                for raw in raw_files:
                    if not isinstance(raw, dict):
                        raise AdapterExportError("export_hash_mismatch", "adapter export file binding is invalid")
                    name = str(raw.get("name") or "")
                    size = raw.get("size_bytes")
                    digest = str(raw.get("sha256") or "")
                    if (
                        not _safe_export_name(name)
                        or name == "ananta_export_manifest.json"
                        or isinstance(size, bool)
                        or not isinstance(size, int)
                        or size < 0
                        or not _is_sha256(digest)
                        or name in expected
                    ):
                        raise AdapterExportError("export_hash_mismatch", "adapter export file binding is invalid")
                    expected[name] = (size, digest)
                if set(names) != set(expected) | {"ananta_export_manifest.json"}:
                    raise AdapterExportError("export_hash_mismatch", "adapter export entries differ from manifest")
                for name, (expected_size, expected_digest) in expected.items():
                    info = archive.getinfo(name)
                    if info.file_size != expected_size:
                        raise AdapterExportError("export_hash_mismatch", "adapter export file size changed")
                    digest = hashlib.sha256()
                    with archive.open(info) as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != expected_digest:
                        raise AdapterExportError("export_hash_mismatch", "adapter export file hash changed")
        except AdapterExportError:
            raise
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise AdapterExportError("export_hash_mismatch", "adapter export verification failed") from exc
        return _file_sha256(path)

    @staticmethod
    def _write_zip(output: Path, adapter_dir: Path, manifest: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".lora-export-", suffix=".zip", dir=str(output.parent))
            os.close(descriptor)
            temporary = Path(name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for source in sorted(path for path in adapter_dir.rglob("*") if path.is_file()):
                    relative = source.relative_to(adapter_dir).as_posix()
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o600 << 16
                    with source.open("rb") as input_handle, archive.open(info, "w") as output_handle:
                        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
                info = zipfile.ZipInfo("ananta_export_manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(
                    info,
                    json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, output)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_export_name(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _scope_directory(tenant_id: str | None, owner_subject: str | None) -> str:
    tenant = str(tenant_id or "").strip()
    owner = str(owner_subject or "").strip()
    if not tenant and not owner:
        return "legacy"
    if not tenant or not owner:
        raise AdapterExportError(
            "adapter_scope_invalid",
            "tenant_id and owner_subject must be provided together",
        )
    return hashlib.sha256(f"{tenant}\0{owner}".encode("utf-8")).hexdigest()[:32]


def _tenant_scope_digest(tenant_id: str, owner_subject: str) -> str:
    return hashlib.sha256(
        (
            "ananta.ml-intern-training.scope.v1\x00"
            f"{tenant_id}\x00{owner_subject}"
        ).encode("utf-8")
    ).hexdigest()
