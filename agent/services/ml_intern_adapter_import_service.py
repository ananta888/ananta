"""Quarantined, bounded and atomic import of existing PEFT/LoRA adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityError,
    MlInternArtifactSecurityService,
)


class AdapterImportError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AdapterImportCompensationToken:
    """Unforgeable, request-local authority to remove one newly-created import."""

    tenant_key: str
    owner_key: str
    adapter_id: str
    version: str
    content_sha256: str
    artifact_relative: str
    nonce: str


@dataclass(frozen=True)
class AdapterImportOutcome:
    """Import summary plus optional compensation authority for its creator."""

    summary: dict[str, Any]
    compensation_token: AdapterImportCompensationToken | None


_IMPORT_LOCK = threading.RLock()
_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tar.gz", ".tgz"}
_ARCHIVE_MEDIA_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/gzip",
    "application/octet-stream",
}


class MlInternAdapterImportService:
    """Validate in quarantine, promote once, then atomically register metadata."""

    def __init__(
        self,
        *,
        storage_root: str | Path,
        security: MlInternArtifactSecurityService | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._security = security or MlInternArtifactSecurityService(storage_root=storage_root)
        self._id_factory = id_factory or (lambda: f"imp-{uuid.uuid4().hex}")
        self._clock = clock or time.time
        self._transaction = InterProcessFileTransaction(
            Path(storage_root) / ".adapter-import-registry.lock"
        )

    def import_archive(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        stream: BinaryIO,
        filename: str,
        media_type: str,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None = None,
        declared_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        return self._import_archive(
            tenant_id=tenant_id,
            principal_id=principal_id,
            stream=stream,
            filename=filename,
            media_type=media_type,
            adapter_id=adapter_id,
            version=version,
            expected_base_model=expected_base_model,
            idempotency_key=idempotency_key,
            declared_size=declared_size,
            expected_sha256=expected_sha256,
            create_compensation_token=False,
        ).summary

    def import_archive_with_receipt(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        stream: BinaryIO,
        filename: str,
        media_type: str,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None = None,
        declared_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> AdapterImportOutcome:
        """Import and grant compensation authority only when this call created it."""

        return self._import_archive(
            tenant_id=tenant_id,
            principal_id=principal_id,
            stream=stream,
            filename=filename,
            media_type=media_type,
            adapter_id=adapter_id,
            version=version,
            expected_base_model=expected_base_model,
            idempotency_key=idempotency_key,
            declared_size=declared_size,
            expected_sha256=expected_sha256,
            create_compensation_token=True,
        )

    def _import_archive(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        stream: BinaryIO,
        filename: str,
        media_type: str,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None = None,
        declared_size: int | None = None,
        expected_sha256: str | None = None,
        create_compensation_token: bool,
    ) -> AdapterImportOutcome:
        extension = _archive_extension(filename)
        if extension not in _ARCHIVE_EXTENSIONS:
            raise AdapterImportError("adapter_archive_extension_invalid", "adapter archive must be ZIP or TAR")
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        import_id = self._new_import_id()
        quarantine_relative = f"tenants/{tenant_key}/adapter-quarantine/{import_id}"
        archive_relative = f"{quarantine_relative}/upload{extension}"
        try:
            stored = self._security.store_upload(
                stream,
                destination_relative=archive_relative,
                filename=filename,
                media_type=media_type,
                allowed_extensions=_ARCHIVE_EXTENSIONS,
                allowed_media_types=_ARCHIVE_MEDIA_TYPES,
                content_kind="zip" if extension == ".zip" else "tar",
                declared_size=declared_size,
                expected_sha256=expected_sha256,
                tenant_bytes_used=self._tenant_bytes(tenant_key),
            )
            archive_path = self._security.resolve_relative(stored.relative_path, must_exist=True)
            extracted = self._security.extract_archive(
                archive_path,
                destination_relative=f"{quarantine_relative}/extracted",
                archive_kind="zip" if extension == ".zip" else "tar",
            )
            adapter_root = self._locate_adapter_root(
                self._security.resolve_relative(extracted.relative_path, must_exist=True)
            )
            return self._finalize_import(
                tenant_key=tenant_key,
                owner_key=owner_key,
                adapter_root=adapter_root,
                adapter_id=adapter_id,
                version=version,
                expected_base_model=expected_base_model,
                idempotency_key=idempotency_key,
                source_sha256=stored.sha256,
                create_compensation_token=create_compensation_token,
            )
        except ArtifactSecurityError as exc:
            raise AdapterImportError(exc.reason_code, str(exc)) from exc
        finally:
            self._security.remove_relative_tree(quarantine_relative)

    def import_files(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        adapter_config_stream: BinaryIO,
        adapter_weights_stream: BinaryIO,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None = None,
        adapter_config_size: int | None = None,
        adapter_weights_size: int | None = None,
    ) -> dict[str, Any]:
        return self._import_files(
            tenant_id=tenant_id,
            principal_id=principal_id,
            adapter_config_stream=adapter_config_stream,
            adapter_weights_stream=adapter_weights_stream,
            adapter_id=adapter_id,
            version=version,
            expected_base_model=expected_base_model,
            idempotency_key=idempotency_key,
            adapter_config_size=adapter_config_size,
            adapter_weights_size=adapter_weights_size,
            create_compensation_token=False,
        ).summary

    def import_files_with_receipt(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        adapter_config_stream: BinaryIO,
        adapter_weights_stream: BinaryIO,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None = None,
        adapter_config_size: int | None = None,
        adapter_weights_size: int | None = None,
    ) -> AdapterImportOutcome:
        """Import a two-file adapter with request-scoped compensation authority."""

        return self._import_files(
            tenant_id=tenant_id,
            principal_id=principal_id,
            adapter_config_stream=adapter_config_stream,
            adapter_weights_stream=adapter_weights_stream,
            adapter_id=adapter_id,
            version=version,
            expected_base_model=expected_base_model,
            idempotency_key=idempotency_key,
            adapter_config_size=adapter_config_size,
            adapter_weights_size=adapter_weights_size,
            create_compensation_token=True,
        )

    def _import_files(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        adapter_config_stream: BinaryIO,
        adapter_weights_stream: BinaryIO,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None = None,
        adapter_config_size: int | None = None,
        adapter_weights_size: int | None = None,
        create_compensation_token: bool,
    ) -> AdapterImportOutcome:
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        import_id = self._new_import_id()
        quarantine_relative = f"tenants/{tenant_key}/adapter-quarantine/{import_id}/files"
        tenant_bytes = self._tenant_bytes(tenant_key)
        try:
            config = self._security.store_upload(
                adapter_config_stream,
                destination_relative=f"{quarantine_relative}/adapter_config.json",
                filename="adapter_config.json",
                media_type="application/json",
                allowed_extensions={".json"},
                allowed_media_types={"application/json"},
                content_kind="json",
                declared_size=adapter_config_size,
                tenant_bytes_used=tenant_bytes,
            )
            weights = self._security.store_upload(
                adapter_weights_stream,
                destination_relative=f"{quarantine_relative}/adapter_model.safetensors",
                filename="adapter_model.safetensors",
                media_type="application/octet-stream",
                allowed_extensions={".safetensors"},
                allowed_media_types={"application/octet-stream", "application/safetensors"},
                content_kind="safetensors",
                declared_size=adapter_weights_size,
                request_bytes_used=config.size_bytes,
                tenant_bytes_used=tenant_bytes + config.size_bytes,
            )
            source_sha256 = hashlib.sha256(f"{config.sha256}:{weights.sha256}".encode("ascii")).hexdigest()
            adapter_root = self._security.resolve_relative(quarantine_relative, must_exist=True)
            return self._finalize_import(
                tenant_key=tenant_key,
                owner_key=owner_key,
                adapter_root=adapter_root,
                adapter_id=adapter_id,
                version=version,
                expected_base_model=expected_base_model,
                idempotency_key=idempotency_key,
                source_sha256=source_sha256,
                create_compensation_token=create_compensation_token,
            )
        except ArtifactSecurityError as exc:
            raise AdapterImportError(exc.reason_code, str(exc)) from exc
        finally:
            self._security.remove_relative_tree(f"tenants/{tenant_key}/adapter-quarantine/{import_id}")

    def list_imports(self, *, tenant_id: str, principal_id: str) -> list[dict[str, Any]]:
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        records = self._load_registry(tenant_key)
        return [self._read_model(row) for row in records if row.get("owner_key") == owner_key]

    def get_import(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        adapter_id: str,
        version: str,
    ) -> dict[str, Any]:
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        normalized_id = self._security.validate_identifier(adapter_id, field_name="adapter_id")
        normalized_version = self._security.validate_identifier(version, field_name="version")
        for row in self._load_registry(tenant_key):
            if (
                row.get("owner_key") == owner_key
                and row.get("adapter_id") == normalized_id
                and row.get("version") == normalized_version
            ):
                return self._read_model(row)
        raise AdapterImportError("adapter_import_not_found", "adapter import does not exist")

    def resolve_artifact_path(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        adapter_id: str,
        version: str,
    ) -> Path:
        """Resolve a verified artifact for another trusted Hub service.

        This infrastructure seam deliberately returns a path only in-process;
        route read models continue to expose hashes and opaque IDs exclusively.
        """

        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        normalized_id = self._security.validate_identifier(adapter_id, field_name="adapter_id")
        normalized_version = self._security.validate_identifier(version, field_name="version")
        for row in self._load_registry(tenant_key):
            if (
                row.get("owner_key") == owner_key
                and row.get("adapter_id") == normalized_id
                and row.get("version") == normalized_version
            ):
                relative = str(row.get("artifact_relative") or "")
                if not relative:
                    raise AdapterImportError("adapter_artifact_missing", "adapter artifact is missing")
                path = self._security.resolve_relative(relative, must_exist=True)
                self._security.validate_adapter_tree(path)
                return path
        raise AdapterImportError("adapter_import_not_found", "adapter import does not exist")

    def commit_import(self, token: AdapterImportCompensationToken) -> bool:
        """Revoke request-local compensation authority after domain publication."""

        with _IMPORT_LOCK, self._transaction:
            records = self._load_registry(token.tenant_key)
            match = self._compensatable_record(records, token)
            if match is None:
                return False
            _index, record = match
            record.pop("compensation_digest", None)
            try:
                self._write_registry(token.tenant_key, records)
            except Exception as exc:
                raise AdapterImportError(
                    "adapter_import_commit_failed",
                    "adapter import compensation authority could not be revoked",
                ) from exc
            return True

    def compensate_import(self, token: AdapterImportCompensationToken) -> None:
        """Remove exactly the import exclusively created by the token holder.

        The artifact is first moved out of its canonical location. If the
        registry write fails, it is restored before an error is returned.
        """

        with _IMPORT_LOCK, self._transaction:
            records = self._load_registry(token.tenant_key)
            match = self._compensatable_record(records, token)
            if match is None:
                raise AdapterImportError(
                    "adapter_import_compensation_stale",
                    "adapter import is no longer exclusively owned by this request",
                )
            index, record = match
            artifact_relative = str(record.get("artifact_relative") or "")
            artifact_path = self._security.resolve_relative(artifact_relative)
            tombstone_relative = (
                f"tenants/{token.tenant_key}/adapter-compensation/"
                f"{_compensation_digest(token.nonce)[:32]}"
            )
            tombstone_path = self._security.resolve_relative(tombstone_relative)
            moved = False
            if artifact_path.exists():
                try:
                    inspected = self._security.validate_adapter_tree(artifact_path)
                except ArtifactSecurityError as exc:
                    raise AdapterImportError(
                        "adapter_import_compensation_hash_mismatch",
                        "adapter artifact changed after import and was not removed",
                    ) from exc
                actual_content_sha256 = _adapter_content_sha256(
                    str(record.get("base_model") or ""),
                    list(inspected.get("files") or []),
                )
                if not hmac.compare_digest(actual_content_sha256, token.content_sha256):
                    raise AdapterImportError(
                        "adapter_import_compensation_hash_mismatch",
                        "adapter artifact changed after import and was not removed",
                    )
                if tombstone_path.exists():
                    raise AdapterImportError(
                        "adapter_import_compensation_conflict",
                        "adapter compensation destination already exists",
                    )
                tombstone_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    os.replace(artifact_path, tombstone_path)
                    moved = True
                except OSError as exc:
                    raise AdapterImportError(
                        "adapter_import_compensation_failed",
                        "adapter artifact could not be isolated for compensation",
                    ) from exc

            remaining = records[:index] + records[index + 1 :]
            try:
                self._write_registry(token.tenant_key, remaining)
            except Exception as exc:
                if moved:
                    try:
                        os.replace(tombstone_path, artifact_path)
                    except OSError as restore_exc:
                        raise AdapterImportError(
                            "adapter_import_compensation_restore_failed",
                            "adapter import registry failed and its artifact could not be restored",
                        ) from restore_exc
                raise AdapterImportError(
                    "adapter_import_compensation_failed",
                    "adapter import registry entry could not be compensated",
                ) from exc

            if moved:
                try:
                    self._security.remove_relative_tree(tombstone_relative)
                except (ArtifactSecurityError, OSError) as exc:
                    raise AdapterImportError(
                        "adapter_import_compensation_cleanup_failed",
                        "isolated adapter files could not be removed",
                    ) from exc

    @staticmethod
    def _compensatable_record(
        records: list[dict[str, Any]],
        token: AdapterImportCompensationToken,
    ) -> tuple[int, dict[str, Any]] | None:
        expected_digest = _compensation_digest(token.nonce)
        for index, record in enumerate(records):
            actual_digest = str(record.get("compensation_digest") or "")
            if (
                record.get("schema") == "mlintern_adapter_import_record.v1"
                and record.get("owner_key") == token.owner_key
                and record.get("adapter_id") == token.adapter_id
                and record.get("version") == token.version
                and record.get("content_sha256") == token.content_sha256
                and record.get("artifact_relative") == token.artifact_relative
                and actual_digest
                and hmac.compare_digest(actual_digest, expected_digest)
            ):
                return index, record
        return None

    def _finalize_import(
        self,
        *,
        tenant_key: str,
        owner_key: str,
        adapter_root: Path,
        adapter_id: str,
        version: str,
        expected_base_model: str,
        idempotency_key: str | None,
        source_sha256: str,
        create_compensation_token: bool,
    ) -> AdapterImportOutcome:
        normalized_id = self._security.validate_identifier(adapter_id, field_name="adapter_id")
        normalized_version = self._security.validate_identifier(version, field_name="version")
        base_model = _base_model_id(expected_base_model)
        inspected = self._security.validate_adapter_tree(adapter_root)
        config = inspected["config"]
        configured_base = str(
            config.get("base_model_name_or_path")
            or config.get("base_model")
            or config.get("base_model_id")
            or ""
        ).strip()
        if not configured_base:
            raise AdapterImportError("adapter_base_model_missing", "adapter config has no base-model binding")
        if configured_base != base_model:
            raise AdapterImportError(
                "adapter_base_model_mismatch",
                "adapter base model differs from the requested model",
            )
        peft_type = str(config.get("peft_type") or "LORA").strip().upper()
        if peft_type not in {"LORA", "ADALORA"}:
            raise AdapterImportError("adapter_type_not_allowed", "only LoRA-compatible PEFT adapters are accepted")
        self._validate_optional_manifest(
            adapter_root,
            inspected=inspected,
            adapter_id=normalized_id,
            version=normalized_version,
            base_model=base_model,
        )
        files = inspected["files"]
        content_sha256 = _adapter_content_sha256(base_model, files)
        idem_digest = _idempotency_digest(owner_key, idempotency_key) if idempotency_key else None
        expected_files = {str(row["name"]): str(row["sha256"]) for row in files}
        destination_relative = (
            f"tenants/{tenant_key}/adapters/{normalized_id}/{normalized_version}/{content_sha256[:24]}"
        )

        with _IMPORT_LOCK, self._transaction:
            records = self._load_registry(tenant_key)
            for existing in records:
                if existing.get("owner_key") != owner_key:
                    continue
                if idem_digest and existing.get("idempotency_digest") == idem_digest:
                    if existing.get("content_sha256") != content_sha256:
                        raise AdapterImportError(
                            "idempotency_conflict",
                            "idempotency key was already used for different adapter content",
                        )
                    return self._replay_outcome(tenant_key, records, existing)
                if existing.get("content_sha256") == content_sha256:
                    return self._replay_outcome(tenant_key, records, existing)
                if existing.get("adapter_id") == normalized_id and existing.get("version") == normalized_version:
                    raise AdapterImportError(
                        "adapter_version_conflict",
                        "adapter ID and version already refer to different content",
                    )
            if self._tenant_bytes(tenant_key) + int(inspected["total_bytes"]) > self._security.policy.max_tenant_bytes:
                raise AdapterImportError("tenant_quota_exceeded", "adapter import exceeds the tenant storage quota")

            promoted = False
            try:
                artifact_relative = self._security.promote_verified_tree(
                    adapter_root,
                    destination_relative=destination_relative,
                    expected_files=expected_files,
                )
                promoted = True
                now = datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat()
                compensation_nonce = secrets.token_urlsafe(32) if create_compensation_token else None
                record = {
                    "schema": "mlintern_adapter_import_record.v1",
                    "adapter_id": normalized_id,
                    "version": normalized_version,
                    "display_name": normalized_id,
                    "base_model": base_model,
                    "method": "lora",
                    "status": "imported_pending_evaluation",
                    "owner_key": owner_key,
                    "content_sha256": content_sha256,
                    "source_sha256": source_sha256,
                    "artifact_relative": artifact_relative,
                    "files": files,
                    "safetensors": inspected["safetensors"],
                    "total_bytes": int(inspected["total_bytes"]),
                    "idempotency_digest": idem_digest,
                    "created_at": now,
                    "updated_at": now,
                }
                if compensation_nonce is not None:
                    record["compensation_digest"] = _compensation_digest(compensation_nonce)
                records.append(record)
                self._write_registry(tenant_key, records)
                token = (
                    AdapterImportCompensationToken(
                        tenant_key=tenant_key,
                        owner_key=owner_key,
                        adapter_id=normalized_id,
                        version=normalized_version,
                        content_sha256=content_sha256,
                        artifact_relative=artifact_relative,
                        nonce=compensation_nonce,
                    )
                    if compensation_nonce is not None
                    else None
                )
                return AdapterImportOutcome(
                    summary=self._read_model(record),
                    compensation_token=token,
                )
            except ArtifactSecurityError as exc:
                raise AdapterImportError(exc.reason_code, str(exc)) from exc
            except Exception:
                if promoted:
                    self._security.remove_relative_tree(destination_relative)
                raise

    def _replay_outcome(
        self,
        tenant_key: str,
        records: list[dict[str, Any]],
        existing: dict[str, Any],
    ) -> AdapterImportOutcome:
        # Observing a just-created import transfers it out of the creator's
        # exclusive ownership. Revoke the earlier deletion authority before
        # returning so a concurrent replay can never lose shared artifacts.
        if existing.pop("compensation_digest", None) is not None:
            self._write_registry(tenant_key, records)
        return AdapterImportOutcome(summary=self._read_model(existing), compensation_token=None)

    def _validate_optional_manifest(
        self,
        adapter_root: Path,
        *,
        inspected: dict[str, Any],
        adapter_id: str,
        version: str,
        base_model: str,
    ) -> None:
        path = adapter_root / "adapter_manifest.json"
        if not path.exists():
            return
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterImportError("adapter_manifest_invalid", "adapter manifest is invalid JSON") from exc
        if not isinstance(manifest, dict):
            raise AdapterImportError("adapter_manifest_invalid", "adapter manifest must be an object")
        expected_values = {
            "adapter_id": adapter_id,
            "version": version,
            "base_model": base_model,
        }
        for key, expected in expected_values.items():
            if key in manifest and str(manifest[key]) != expected:
                raise AdapterImportError("adapter_manifest_mismatch", f"adapter manifest {key} is inconsistent")
        declared_files = manifest.get("files")
        if declared_files is None:
            return
        actual = {str(row["name"]): (str(row["sha256"]), int(row["size_bytes"])) for row in inspected["files"]}
        try:
            if isinstance(declared_files, dict):
                if any(not isinstance(value, dict) for value in declared_files.values()):
                    raise TypeError("manifest file entry must be an object")
                normalized = {
                    str(name): (str(value.get("sha256") or ""), int(value.get("size_bytes") or -1))
                    for name, value in declared_files.items()
                }
            elif isinstance(declared_files, list):
                if any(not isinstance(value, dict) for value in declared_files):
                    raise TypeError("manifest file entry must be an object")
                normalized = {
                    str(value.get("name") or ""): (
                        str(value.get("sha256") or ""),
                        int(value.get("size_bytes") or -1),
                    )
                    for value in declared_files
                }
            else:
                raise TypeError("manifest files must be a list or object")
        except (OverflowError, TypeError, ValueError) as exc:
            raise AdapterImportError(
                "adapter_manifest_invalid",
                "adapter manifest file entries are invalid",
            ) from exc
        # The manifest does not need to hash itself, but every other promoted file
        # must be bound exactly.
        actual_without_manifest = {key: value for key, value in actual.items() if key != "adapter_manifest.json"}
        if normalized != actual_without_manifest:
            raise AdapterImportError("adapter_manifest_mismatch", "adapter manifest file hashes are inconsistent")

    def _locate_adapter_root(self, extracted_root: Path) -> Path:
        if (extracted_root / "adapter_config.json").is_file():
            return extracted_root
        visible = [path for path in extracted_root.iterdir() if path.name not in {"__MACOSX", ".DS_Store"}]
        directories = [path for path in visible if path.is_dir() and not path.is_symlink()]
        files = [path for path in visible if path.is_file()]
        if not files and len(directories) == 1 and (directories[0] / "adapter_config.json").is_file():
            return directories[0]
        raise AdapterImportError(
            "adapter_root_ambiguous",
            "archive must contain adapter files at root or in one wrapper directory",
        )

    def _scope_keys(self, tenant_id: str, principal_id: str) -> tuple[str, str]:
        tenant_key = self._security.tenant_storage_key(tenant_id)
        principal = str(principal_id or "").strip()
        if not principal:
            raise AdapterImportError("principal_id_required", "principal_id is required")
        return tenant_key, hashlib.sha256(principal.encode("utf-8")).hexdigest()

    def _new_import_id(self) -> str:
        import_id = str(self._id_factory())
        if not import_id.startswith("imp-"):
            raise AdapterImportError("invalid_import_id", "adapter import ID is invalid")
        self._security.validate_identifier(import_id, field_name="import_id")
        return import_id

    def _registry_relative(self, tenant_key: str) -> str:
        return f"tenants/{tenant_key}/adapter-import-registry.json"

    def _load_registry(self, tenant_key: str) -> list[dict[str, Any]]:
        path = self._security.resolve_relative(self._registry_relative(tenant_key))
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterImportError("adapter_registry_corrupt", "adapter import registry cannot be read") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "mlintern_adapter_import_registry.v1":
            raise AdapterImportError("adapter_registry_corrupt", "adapter import registry schema is invalid")
        records = payload.get("records")
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise AdapterImportError("adapter_registry_corrupt", "adapter import registry records are invalid")
        return list(records)

    def _write_registry(self, tenant_key: str, records: list[dict[str, Any]]) -> None:
        self._security.atomic_write_json(
            self._registry_relative(tenant_key),
            {
                "schema": "mlintern_adapter_import_registry.v1",
                "records": records,
                "updated_at": datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _read_model(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "mlintern_adapter_import_summary.v1",
            "adapter_id": record.get("adapter_id"),
            "version": record.get("version"),
            "display_name": record.get("display_name"),
            "base_model": record.get("base_model"),
            "method": record.get("method"),
            "status": record.get("status"),
            "content_sha256": record.get("content_sha256"),
            "source_sha256": record.get("source_sha256"),
            "total_bytes": int(record.get("total_bytes") or 0),
            "files": [
                {"name": row.get("name"), "sha256": row.get("sha256"), "size_bytes": int(row.get("size_bytes") or 0)}
                for row in record.get("files") or []
                if isinstance(row, dict)
            ],
            "safetensors": dict(record.get("safetensors") or {}),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _tenant_bytes(self, tenant_key: str) -> int:
        root = self._security.resolve_relative(f"tenants/{tenant_key}")
        if not root.exists():
            return 0
        total = 0
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
                if total > self._security.policy.max_tenant_bytes:
                    break
        return total


def _archive_extension(filename: str) -> str:
    clean = Path(str(filename or "")).name
    if not clean or clean != str(filename):
        raise AdapterImportError("invalid_filename", "adapter archive filename is invalid")
    lowered = clean.lower()
    for suffix in sorted(_ARCHIVE_EXTENSIONS, key=len, reverse=True):
        if lowered.endswith(suffix):
            return suffix
    return Path(lowered).suffix


def _idempotency_digest(owner_key: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise AdapterImportError("invalid_idempotency_key", "idempotency key is invalid")
    return hashlib.sha256(f"{owner_key}:{normalized}".encode("utf-8")).hexdigest()


def _compensation_digest(nonce: str) -> str:
    return hashlib.sha256(f"mlintern-adapter-import-compensation-v1:{nonce}".encode("utf-8")).hexdigest()


def _adapter_content_sha256(base_model: str, files: list[dict[str, Any]]) -> str:
    bound_files = sorted(
        (
            {"name": str(row.get("name") or ""), "sha256": str(row.get("sha256") or "")}
            for row in files
            if isinstance(row, dict)
        ),
        key=lambda row: row["name"],
    )
    return hashlib.sha256(
        json.dumps(
            {"base_model": base_model, "files": bound_files},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _base_model_id(value: str) -> str:
    normalized = str(value or "").strip()
    raw_parts = normalized.split("/")
    if (
        not normalized
        or len(normalized) > 512
        or any(ord(char) < 32 for char in normalized)
        or "\\" in normalized
        or normalized.startswith(("/", "~"))
        or any(part in {"", ".", ".."} for part in raw_parts)
        or (raw_parts and raw_parts[0].endswith(":"))
        or PurePosixPath(normalized).is_absolute()
    ):
        raise AdapterImportError("invalid_base_model", "expected_base_model must be an opaque model ID")
    return normalized
