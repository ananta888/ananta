"""Tenant/principal-bound filesystem catalog for curated ml_intern datasets.

The catalog is an additive persistence adapter until the durable training
repository is wired.  Public methods only accept opaque dataset IDs; local
paths remain private implementation details.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityError,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_dataset_validation_service import (
    MlInternDatasetValidationService,
    get_dataset_validation_service,
)
from agent.services.ml_intern_lora_dataset_build_service import (
    DatasetBuildError,
    MlInternLoraDatasetBuildService,
)


class DatasetCatalogError(ValueError):
    """Dataset catalog rejection with a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_CATALOG_LOCK = threading.RLock()
_DATASET_ID = re.compile(r"^ds-[a-f0-9]{32}$")
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email_address", re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE)),
    ("ipv4_address", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")),
    ("phone_number", re.compile(r"(?<!\w)(?:\+?\d[\d ()/.-]{7,}\d)(?!\w)")),
    ("government_id", re.compile(r"\b(?:SSN|Sozialversicherungsnummer)\s*[:=]\s*[A-Z0-9 -]{6,}", re.IGNORECASE)),
)


class MlInternDatasetCatalogService:
    """Catalog, validate and atomically version immutable training datasets."""

    def __init__(
        self,
        *,
        storage_root: str | Path,
        security: MlInternArtifactSecurityService | None = None,
        validator: MlInternDatasetValidationService | None = None,
        builder_factory: Callable[[Path], MlInternLoraDatasetBuildService] | None = None,
        audit_sink: Callable[[str, dict[str, Any]], None] | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._security = security or MlInternArtifactSecurityService(storage_root=storage_root)
        self._validator = validator or get_dataset_validation_service()
        self._builder_factory = builder_factory or (
            lambda root: MlInternLoraDatasetBuildService(dataset_root=root, validator=self._validator)
        )
        self._audit_sink = audit_sink or (lambda _action, _details: None)
        self._id_factory = id_factory or (lambda: f"ds-{uuid.uuid4().hex}")
        self._clock = clock or time.time
        self._transaction = InterProcessFileTransaction(
            Path(storage_root) / ".dataset-catalog.lock"
        )

    @property
    def max_records(self) -> int:
        return self._security.policy.max_records

    def create_from_upload(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        stream: BinaryIO,
        filename: str,
        media_type: str,
        name: str | None = None,
        dataset_format: str = "instruction",
        idempotency_key: str | None = None,
        declared_size: int | None = None,
        expected_sha256: str | None = None,
        max_examples: int | None = None,
    ) -> dict[str, Any]:
        extension = _dataset_extension(filename)
        if extension not in {".json", ".jsonl"}:
            raise DatasetCatalogError("dataset_extension_not_allowed", "dataset upload must be JSON or JSONL")
        fmt = _dataset_format(dataset_format)
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        dataset_id = self._new_dataset_id()
        dataset_relative = self._dataset_relative(tenant_key, dataset_id)
        source_name = f"source{extension}"
        source_relative = f"{dataset_relative}/{source_name}"
        tenant_bytes = self._tenant_bytes(tenant_key)

        try:
            stored = self._security.store_upload(
                stream,
                destination_relative=source_relative,
                filename=filename,
                media_type=media_type,
                allowed_extensions={".json", ".jsonl"},
                allowed_media_types={"application/json", "application/jsonl", "application/x-ndjson", "text/jsonl"},
                content_kind="jsonl" if extension == ".jsonl" else "json",
                declared_size=declared_size,
                expected_sha256=expected_sha256,
                tenant_bytes_used=tenant_bytes,
            )
            dataset_dir = self._security.resolve_relative(dataset_relative, must_exist=True)
            input_count = self._count_source_records(dataset_dir / source_name, extension)
            self._security.enforce_record_quota(input_count)
            request_digest = _request_digest(
                {
                    "source_sha256": stored.sha256,
                    "dataset_format": fmt,
                    "max_examples": min(max_examples or self.max_records, self.max_records),
                }
            )
            with _CATALOG_LOCK, self._transaction:
                existing = self._resolve_idempotency(
                    tenant_key=tenant_key,
                    owner_key=owner_key,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                if existing is not None:
                    self._security.remove_relative_tree(dataset_relative)
                    return self.get_dataset(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        dataset_id=existing,
                    )

                builder = self._builder_factory(dataset_dir)
                build = builder.build_dataset(
                    {
                        "source_paths": [source_name],
                        "output_path": "train.jsonl",
                        "format": fmt,
                        "max_examples": min(max_examples or self.max_records, self.max_records),
                        "require_secret_scan": True,
                    }
                )
                if not build.dataset_path:
                    raise DatasetCatalogError("dataset_build_failed", "; ".join(build.errors) or "dataset build failed")
                train_path = dataset_dir / "train.jsonl"
                _sanitize_built_dataset(train_path)
                ingress_validation = self._validator.validate(train_path, require_secret_scan=True)
                if self._tenant_bytes(tenant_key) > self._security.policy.max_tenant_bytes:
                    raise DatasetCatalogError(
                        "tenant_quota_exceeded",
                        "dataset build exceeds the tenant storage quota",
                    )
                now = _iso_time(self._clock())
                validation_state = "failed" if not ingress_validation.ok else "pending"
                metadata = {
                    "schema": "mlintern_dataset_catalog_record.v1",
                    "dataset_id": dataset_id,
                    "tenant_key": tenant_key,
                    "owner_key": owner_key,
                    "name": _bounded_name(name or Path(filename).stem),
                    "source_filename": Path(filename).name[:255],
                    "source_sha256": stored.sha256,
                    "source_bytes": stored.size_bytes,
                    "dataset_sha256": _hash_file(train_path),
                    "dataset_bytes": train_path.stat().st_size,
                    "format_type": fmt,
                    "status": "quarantined" if validation_state == "failed" else "ready_for_validation",
                    "record_count": build.written_records,
                    "input_record_count": input_count,
                    "duplicate_count": build.duplicate_count,
                    "rejected_record_count": len(build.skipped_records),
                    "partitions": {
                        "train": {
                            "relative_path": "train.jsonl",
                            "record_count": build.written_records,
                            "sha256": _hash_file(train_path),
                            "size_bytes": train_path.stat().st_size,
                        }
                    },
                    "split_source": {
                        "relative_path": "train.jsonl",
                        "record_count": build.written_records,
                        "sha256": _hash_file(train_path),
                        "size_bytes": train_path.stat().st_size,
                    },
                    "split": {"status": "not_split", "validation_record_count": 0},
                    "validation": {
                        "status": validation_state,
                        "trainable": False,
                        "summary": _validation_summary(ingress_validation.to_dict()),
                    },
                    "references": [],
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                self._write_metadata(tenant_key, dataset_id, metadata)
                self._record_idempotency(
                    tenant_key=tenant_key,
                    owner_key=owner_key,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    dataset_id=dataset_id,
                )
                return self._read_model(metadata)
        except (ArtifactSecurityError, DatasetBuildError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._security.remove_relative_tree(dataset_relative)
            if isinstance(exc, ArtifactSecurityError):
                raise DatasetCatalogError(exc.reason_code, str(exc)) from exc
            if isinstance(exc, DatasetCatalogError):
                raise
            raise DatasetCatalogError("dataset_ingress_failed", "dataset ingress failed validation") from exc
        except Exception:
            self._security.remove_relative_tree(dataset_relative)
            raise

    def create_from_records(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        records: Iterable[dict[str, Any]],
        name: str,
        dataset_format: str = "instruction",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        temporary = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        count = 0
        total = 0
        try:
            for record in records:
                if not isinstance(record, dict):
                    raise DatasetCatalogError("invalid_dataset_record", "curated records must be JSON objects")
                count += 1
                self._security.enforce_record_quota(count)
                row = (json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
                total += len(row)
                if total > self._security.policy.max_file_bytes:
                    raise DatasetCatalogError("file_quota_exceeded", "curated records exceed the dataset size limit")
                temporary.write(row)
            if count == 0:
                raise DatasetCatalogError("empty_dataset", "at least one curated record is required")
            temporary.seek(0)
            return self.create_from_upload(
                tenant_id=tenant_id,
                principal_id=principal_id,
                stream=temporary,
                filename="curated.jsonl",
                media_type="application/x-ndjson",
                name=name,
                dataset_format=dataset_format,
                idempotency_key=idempotency_key,
                declared_size=total,
            )
        finally:
            temporary.close()

    def list_datasets(self, *, tenant_id: str, principal_id: str) -> list[dict[str, Any]]:
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        base = self._security.resolve_relative(f"tenants/{tenant_key}/datasets")
        if not base.exists():
            return []
        rows = []
        for child in base.iterdir():
            if not child.is_dir() or not _DATASET_ID.fullmatch(child.name):
                continue
            metadata = self._load_metadata(tenant_key, child.name, required=False)
            if metadata and metadata.get("owner_key") == owner_key:
                rows.append(self._read_model(metadata))
        return sorted(rows, key=lambda row: (row.get("created_at") or "", row["dataset_id"]), reverse=True)

    def get_dataset(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> dict[str, Any]:
        metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
        return self._read_model(metadata)

    def get_validation_report(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> dict[str, Any] | None:
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        self._owned_metadata(tenant_id, principal_id, dataset_id)
        path = self._security.resolve_relative(
            f"{self._dataset_relative(tenant_key, dataset_id)}/validation_report.json"
        )
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DatasetCatalogError("validation_report_corrupt", "validation report cannot be read") from exc
        return payload if isinstance(payload, dict) else None

    def validate_dataset(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        allow_sensitive_override: bool = False,
        is_admin: bool = False,
        override_reason: str | None = None,
    ) -> dict[str, Any]:
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
        validated_partitions = {
            name: str(value.get("sha256") or "")
            for name, value in (metadata.get("partitions") or {}).items()
            if isinstance(value, dict)
        }
        reason = str(override_reason or "").strip()
        if allow_sensitive_override and (not is_admin or len(reason) < 10):
            raise DatasetCatalogError(
                "sensitive_override_denied",
                "sensitive-data override requires an admin and a meaningful reason",
            )
        override = {"reason": reason, "overrides": {"sensitive_data": True}} if allow_sensitive_override else None
        train_path = self._partition_path_from_metadata(tenant_key, dataset_id, metadata, "train")
        validation_entry = (metadata.get("partitions") or {}).get("validation")
        pair_errors: list[str] = []
        overlap_count = 0
        if isinstance(validation_entry, dict):
            validation_path = self._partition_path_from_metadata(tenant_key, dataset_id, metadata, "validation")
            pair_train_report, pair_validation_report, pair_errors = self._validator.validate_train_eval_pair(
                train_path,
                validation_path,
                require_secret_scan=True,
            )
            if override is not None:
                train_report = self._validator.validate(
                    train_path,
                    require_secret_scan=True,
                    explicit_override=override,
                )
                validation_report = self._validator.validate(
                    validation_path,
                    require_secret_scan=True,
                    explicit_override=override,
                )
            else:
                train_report = pair_train_report
                validation_report = pair_validation_report
            overlap_count = _record_overlap_count(train_path, validation_path)
            if overlap_count:
                pair_errors.append(f"train/validation semantic record overlap: {overlap_count}")
        else:
            train_report = self._validator.validate(
                train_path,
                require_secret_scan=True,
                explicit_override=override,
            )
            validation_report = None

        pii_findings = _scan_pii(train_path)
        if validation_report is not None:
            validation_path = self._partition_path_from_metadata(tenant_key, dataset_id, metadata, "validation")
            pii_findings.extend(_scan_pii(validation_path, partition="validation"))
        sensitive_blocked = bool(pii_findings) and not allow_sensitive_override
        ok = bool(
            train_report.ok
            and (validation_report is None or validation_report.ok)
            and not pair_errors
            and not sensitive_blocked
        )
        report = {
            "schema": "mlintern_dataset_catalog_validation.v1",
            "dataset_id": dataset_id,
            "ok": ok,
            "reason_codes": _validation_reason_codes(
                train_report.to_dict(),
                validation_report.to_dict() if validation_report else None,
                pair_errors,
                pii_findings,
                allow_sensitive_override,
            ),
            "train": _safe_validation_report(train_report.to_dict()),
            "validation": _safe_validation_report(validation_report.to_dict()) if validation_report else None,
            "pair_errors": pair_errors,
            "semantic_overlap_count": overlap_count,
            "pii_finding_count": len(pii_findings),
            "pii_findings": pii_findings,
            "sensitive_override": {
                "applied": bool(allow_sensitive_override),
                "reason": reason if allow_sensitive_override else None,
            },
            "validated_at": _iso_time(self._clock()),
        }
        with _CATALOG_LOCK, self._transaction:
            current_metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
            current_partitions = {
                name: str(value.get("sha256") or "")
                for name, value in (current_metadata.get("partitions") or {}).items()
                if isinstance(value, dict)
            }
            if current_partitions != validated_partitions:
                raise DatasetCatalogError(
                    "dataset_changed_during_validation",
                    "dataset partitions changed while validation was running; retry validation",
                )
            metadata = current_metadata
            self._security.atomic_write_json(
                f"{self._dataset_relative(tenant_key, dataset_id)}/validation_report.json",
                report,
            )
            metadata["validation"] = {
                "status": "passed" if ok else "failed",
                "trainable": ok,
                "summary": {
                    "error_count": len(train_report.errors)
                    + (len(validation_report.errors) if validation_report else 0),
                    "warning_count": len(train_report.warnings)
                    + (len(validation_report.warnings) if validation_report else 0),
                    "secret_finding_count": len(train_report.secret_findings)
                    + (len(validation_report.secret_findings) if validation_report else 0),
                    "pii_finding_count": len(pii_findings),
                    "pair_error_count": len(pair_errors),
                },
                "validated_at": report["validated_at"],
            }
            metadata["status"] = "validated" if ok else "quarantined"
            self._touch(metadata)
            self._write_metadata(tenant_key, dataset_id, metadata)
        if allow_sensitive_override:
            self._audit_sink(
                "ml_intern_dataset_sensitive_override",
                {"dataset_id": dataset_id, "reason": reason, "pii_finding_count": len(pii_findings)},
            )
        return report

    def mark_referenced(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        reference_id: str,
    ) -> None:
        ref = self._security.validate_identifier(reference_id, field_name="reference_id")
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        with _CATALOG_LOCK, self._transaction:
            metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
            references = sorted(set(str(item) for item in metadata.get("references") or []) | {ref})
            metadata["references"] = references
            self._touch(metadata)
            self._write_metadata(tenant_key, dataset_id, metadata)

    def delete_dataset(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> None:
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        with _CATALOG_LOCK, self._transaction:
            metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
            if metadata.get("references"):
                raise DatasetCatalogError("dataset_referenced", "referenced datasets cannot be deleted")
            self._security.remove_relative_tree(self._dataset_relative(tenant_key, dataset_id))
            self._audit_sink("ml_intern_dataset_deleted", {"dataset_id": dataset_id})

    def assert_deletable(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> None:
        """Fail before a cross-store delete when the catalog owns references."""

        with _CATALOG_LOCK, self._transaction:
            metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
            if metadata.get("references"):
                raise DatasetCatalogError("dataset_referenced", "referenced datasets cannot be deleted")

    @contextmanager
    def open_partition(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str = "train",
    ) -> Iterator[Any]:
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
        path = self._partition_path_from_metadata(tenant_key, dataset_id, metadata, partition)
        try:
            with path.open("r", encoding="utf-8", errors="strict") as handle:
                yield handle
        except UnicodeError as exc:
            raise DatasetCatalogError("dataset_encoding_invalid", "dataset is not valid UTF-8") from exc

    def partition_descriptor(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str = "train",
    ) -> dict[str, Any]:
        metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
        entry = (metadata.get("partitions") or {}).get(_partition_name(partition))
        if not isinstance(entry, dict):
            raise DatasetCatalogError("partition_not_found", "dataset partition does not exist")
        return {
            "dataset_id": dataset_id,
            "partition": _partition_name(partition),
            "record_count": int(entry.get("record_count") or 0),
            "sha256": str(entry.get("sha256") or ""),
            "size_bytes": int(entry.get("size_bytes") or 0),
            "validation_status": str((metadata.get("validation") or {}).get("status") or "pending"),
        }

    @contextmanager
    def open_split_source(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
    ) -> Iterator[Any]:
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
        entry = metadata.get("split_source")
        if not isinstance(entry, dict) or not entry.get("relative_path"):
            raise DatasetCatalogError("split_source_not_found", "dataset has no immutable split source")
        path = self._security.resolve_relative(
            f"{self._dataset_relative(tenant_key, dataset_id)}/{entry['relative_path']}",
            must_exist=True,
        )
        try:
            with path.open("r", encoding="utf-8", errors="strict") as handle:
                yield handle
        except UnicodeError as exc:
            raise DatasetCatalogError("dataset_encoding_invalid", "dataset is not valid UTF-8") from exc

    def split_source_descriptor(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
    ) -> dict[str, Any]:
        metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
        entry = metadata.get("split_source")
        if not isinstance(entry, dict):
            raise DatasetCatalogError("split_source_not_found", "dataset has no immutable split source")
        return {
            "dataset_id": dataset_id,
            "record_count": int(entry.get("record_count") or 0),
            "sha256": str(entry.get("sha256") or ""),
            "size_bytes": int(entry.get("size_bytes") or 0),
        }

    def commit_split(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        train_stream: BinaryIO,
        validation_stream: BinaryIO,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_key, _ = self._scope_keys(tenant_id, principal_id)
        with _CATALOG_LOCK, self._transaction:
            metadata = self._owned_metadata(tenant_id, principal_id, dataset_id)
            revision = int(metadata.get("revision") or 0) + 1
            split_id = f"split-{revision}-{uuid.uuid4().hex[:12]}"
            split_relative = f"{self._dataset_relative(tenant_key, dataset_id)}/splits/{split_id}"
            try:
                tenant_bytes = self._tenant_bytes(tenant_key)
                train = self._security.store_upload(
                    train_stream,
                    destination_relative=f"{split_relative}/train.jsonl",
                    filename="train.jsonl",
                    media_type="application/x-ndjson",
                    allowed_extensions={".jsonl"},
                    allowed_media_types={"application/x-ndjson"},
                    content_kind="jsonl",
                    tenant_bytes_used=tenant_bytes,
                )
                validation = self._security.store_upload(
                    validation_stream,
                    destination_relative=f"{split_relative}/validation.jsonl",
                    filename="validation.jsonl",
                    media_type="application/x-ndjson",
                    allowed_extensions={".jsonl"},
                    allowed_media_types={"application/x-ndjson"},
                    content_kind="jsonl",
                    request_bytes_used=train.size_bytes,
                    tenant_bytes_used=tenant_bytes + train.size_bytes,
                )
                expected_train = str(manifest.get("train_sha256") or "")
                expected_validation = str(manifest.get("validation_sha256") or "")
                if expected_train and expected_train != train.sha256:
                    raise DatasetCatalogError("split_hash_mismatch", "train split hash differs from manifest")
                if expected_validation and expected_validation != validation.sha256:
                    raise DatasetCatalogError("split_hash_mismatch", "validation split hash differs from manifest")
                persisted_manifest = {
                    **dict(manifest),
                    "train_sha256": train.sha256,
                    "validation_sha256": validation.sha256,
                }
                self._security.atomic_write_json(f"{split_relative}/split_manifest.json", persisted_manifest)
                metadata["partitions"] = {
                    "train": {
                        "relative_path": f"splits/{split_id}/train.jsonl",
                        "record_count": int(manifest.get("train_record_count") or 0),
                        "sha256": train.sha256,
                        "size_bytes": train.size_bytes,
                    },
                    "validation": {
                        "relative_path": f"splits/{split_id}/validation.jsonl",
                        "record_count": int(manifest.get("validation_record_count") or 0),
                        "sha256": validation.sha256,
                        "size_bytes": validation.size_bytes,
                    },
                }
                metadata["split"] = {
                    "status": "ready",
                    "split_id": split_id,
                    "algorithm_version": manifest.get("algorithm_version"),
                    "seed": manifest.get("seed"),
                    "validation_ratio": manifest.get("validation_ratio"),
                    "train_record_count": int(manifest.get("train_record_count") or 0),
                    "validation_record_count": int(manifest.get("validation_record_count") or 0),
                }
                metadata["record_count"] = int(manifest.get("train_record_count") or 0)
                metadata["dataset_sha256"] = train.sha256
                metadata["dataset_bytes"] = train.size_bytes
                metadata["validation"] = {"status": "pending", "trainable": False, "summary": {}}
                metadata["status"] = "ready_for_validation"
                metadata["revision"] = revision
                metadata["updated_at"] = _iso_time(self._clock())
                self._write_metadata(tenant_key, dataset_id, metadata)
                return self._read_model(metadata)
            except Exception:
                self._security.remove_relative_tree(split_relative)
                raise

    def copy_partition_to(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        partition: str,
        destination: BinaryIO,
    ) -> None:
        with self.open_partition(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=dataset_id,
            partition=partition,
        ) as source:
            for line in source:
                destination.write(line.encode("utf-8"))

    def _scope_keys(self, tenant_id: str, principal_id: str) -> tuple[str, str]:
        tenant_key = self._security.tenant_storage_key(tenant_id)
        principal = str(principal_id or "").strip()
        if not principal:
            raise DatasetCatalogError("principal_id_required", "principal_id is required")
        owner_key = hashlib.sha256(principal.encode("utf-8")).hexdigest()
        return tenant_key, owner_key

    def _new_dataset_id(self) -> str:
        dataset_id = str(self._id_factory())
        if not _DATASET_ID.fullmatch(dataset_id):
            raise DatasetCatalogError("invalid_dataset_id", "dataset ID factory returned an invalid ID")
        return dataset_id

    @staticmethod
    def _dataset_relative(tenant_key: str, dataset_id: str) -> str:
        if not _DATASET_ID.fullmatch(str(dataset_id or "")):
            raise DatasetCatalogError("invalid_dataset_id", "dataset_id has an invalid format")
        return f"tenants/{tenant_key}/datasets/{dataset_id}"

    def _metadata_relative(self, tenant_key: str, dataset_id: str) -> str:
        return f"{self._dataset_relative(tenant_key, dataset_id)}/metadata.json"

    def _write_metadata(self, tenant_key: str, dataset_id: str, metadata: dict[str, Any]) -> None:
        self._security.atomic_write_json(self._metadata_relative(tenant_key, dataset_id), metadata)

    def _load_metadata(self, tenant_key: str, dataset_id: str, *, required: bool = True) -> dict[str, Any] | None:
        path = self._security.resolve_relative(self._metadata_relative(tenant_key, dataset_id))
        if not path.exists():
            if required:
                raise DatasetCatalogError("dataset_not_found", "dataset does not exist")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DatasetCatalogError("dataset_metadata_corrupt", "dataset metadata cannot be read") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "mlintern_dataset_catalog_record.v1":
            raise DatasetCatalogError("dataset_metadata_corrupt", "dataset metadata schema is invalid")
        return payload

    def _owned_metadata(self, tenant_id: str, principal_id: str, dataset_id: str) -> dict[str, Any]:
        tenant_key, owner_key = self._scope_keys(tenant_id, principal_id)
        metadata = self._load_metadata(tenant_key, dataset_id)
        if metadata is None or metadata.get("owner_key") != owner_key:
            raise DatasetCatalogError("dataset_not_found", "dataset does not exist")
        return metadata

    def _partition_path_from_metadata(
        self,
        tenant_key: str,
        dataset_id: str,
        metadata: dict[str, Any],
        partition: str,
    ) -> Path:
        normalized = _partition_name(partition)
        entry = (metadata.get("partitions") or {}).get(normalized)
        if not isinstance(entry, dict) or not entry.get("relative_path"):
            raise DatasetCatalogError("partition_not_found", "dataset partition does not exist")
        relative = f"{self._dataset_relative(tenant_key, dataset_id)}/{entry['relative_path']}"
        return self._security.resolve_relative(relative, must_exist=True)

    def _read_model(self, metadata: dict[str, Any]) -> dict[str, Any]:
        partitions = metadata.get("partitions") or {}
        return {
            "schema": "mlintern_dataset_summary.v1",
            "dataset_id": metadata["dataset_id"],
            "name": metadata.get("name"),
            "status": metadata.get("status"),
            "format_type": metadata.get("format_type"),
            "record_count": int(metadata.get("record_count") or 0),
            "input_record_count": int(metadata.get("input_record_count") or 0),
            "rejected_record_count": int(metadata.get("rejected_record_count") or 0),
            "duplicate_count": int(metadata.get("duplicate_count") or 0),
            "sha256": metadata.get("dataset_sha256"),
            "size_bytes": int(metadata.get("dataset_bytes") or 0),
            "partitions": {
                key: {
                    "record_count": int(value.get("record_count") or 0),
                    "sha256": value.get("sha256"),
                    "size_bytes": int(value.get("size_bytes") or 0),
                }
                for key, value in partitions.items()
                if isinstance(value, dict)
            },
            "split": dict(metadata.get("split") or {}),
            "validation": dict(metadata.get("validation") or {}),
            "referenced": bool(metadata.get("references")),
            "revision": int(metadata.get("revision") or 0),
            "created_at": metadata.get("created_at"),
            "updated_at": metadata.get("updated_at"),
        }

    def _resolve_idempotency(
        self,
        *,
        tenant_key: str,
        owner_key: str,
        idempotency_key: str | None,
        request_digest: str,
    ) -> str | None:
        if not idempotency_key:
            return None
        key = _idempotency_key(idempotency_key)
        key_digest = hashlib.sha256((owner_key + ":" + key).encode()).hexdigest()
        path = self._security.resolve_relative(
            f"tenants/{tenant_key}/dataset-idempotency/{key_digest}.json"
        )
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetCatalogError("idempotency_state_corrupt", "idempotency state cannot be read") from exc
        if payload.get("request_digest") != request_digest:
            raise DatasetCatalogError("idempotency_conflict", "idempotency key was already used for different content")
        dataset_id = str(payload.get("dataset_id") or "")
        if not _DATASET_ID.fullmatch(dataset_id):
            raise DatasetCatalogError("idempotency_state_corrupt", "idempotency state references an invalid dataset")
        if self._load_metadata(tenant_key, dataset_id, required=False) is None:
            path.unlink(missing_ok=True)
            return None
        return dataset_id

    def _record_idempotency(
        self,
        *,
        tenant_key: str,
        owner_key: str,
        idempotency_key: str | None,
        request_digest: str,
        dataset_id: str,
    ) -> None:
        if not idempotency_key:
            return
        key = _idempotency_key(idempotency_key)
        digest = hashlib.sha256((owner_key + ":" + key).encode()).hexdigest()
        self._security.atomic_write_json(
            f"tenants/{tenant_key}/dataset-idempotency/{digest}.json",
            {"schema": "mlintern_dataset_idempotency.v1", "request_digest": request_digest, "dataset_id": dataset_id},
        )

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

    def _count_source_records(self, path: Path, extension: str) -> int:
        if extension == ".jsonl":
            count = 0
            with path.open("r", encoding="utf-8", errors="strict") as handle:
                for line in handle:
                    if line.strip():
                        count += 1
                        self._security.enforce_record_quota(count)
            return count
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, dict):
            nested = payload.get("records") or payload.get("examples") or payload.get("items")
            count = len(nested) if isinstance(nested, list) else 1
        else:
            raise DatasetCatalogError("invalid_dataset_json", "JSON dataset must contain an object or list")
        self._security.enforce_record_quota(count)
        return count

    def _touch(self, metadata: dict[str, Any]) -> None:
        metadata["revision"] = int(metadata.get("revision") or 0) + 1
        metadata["updated_at"] = _iso_time(self._clock())


def _dataset_extension(filename: str) -> str:
    clean = Path(str(filename or "")).name
    if clean != str(filename) or not clean:
        raise DatasetCatalogError("invalid_filename", "dataset filename is invalid")
    return Path(clean).suffix.lower()


def _dataset_format(value: str) -> str:
    normalized = str(value or "instruction").strip().lower()
    if normalized not in {"instruction", "chat"}:
        raise DatasetCatalogError("invalid_dataset_format", "dataset format must be instruction or chat")
    return normalized


def _partition_name(value: str) -> str:
    normalized = str(value or "train").strip().lower()
    if normalized not in {"train", "validation"}:
        raise DatasetCatalogError("invalid_partition", "partition must be train or validation")
    return normalized


def _bounded_name(value: str) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        raise DatasetCatalogError("dataset_name_required", "dataset name is required")
    return normalized[:160]


def _idempotency_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise DatasetCatalogError("invalid_idempotency_key", "idempotency key is invalid")
    return normalized


def _request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_built_dataset(path: Path) -> None:
    """Remove builder-internal source paths before a dataset becomes visible."""

    descriptor, temp_name = tempfile.mkstemp(prefix=".sanitize-", dir=str(path.parent))
    temporary = Path(temp_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            with path.open("r", encoding="utf-8", errors="strict") as source:
                for line_number, raw_line in enumerate(source, start=1):
                    if not raw_line.strip():
                        continue
                    record = json.loads(raw_line)
                    if not isinstance(record, dict):
                        raise DatasetCatalogError(
                            "invalid_dataset_record",
                            f"built record {line_number} is not an object",
                        )
                    record.pop("source_path", None)
                    target.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
                target.flush()
                os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _validation_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    source = report or {}
    return {
        "error_count": int(source.get("error_count") or 0),
        "warning_count": int(source.get("warning_count") or 0),
        "secret_finding_count": int(source.get("secret_finding_count") or 0),
    }


def _scan_pii(path: Path, *, partition: str = "train") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            for reason_code, pattern in _PII_PATTERNS:
                if pattern.search(line):
                    findings.append({"partition": partition, "line": line_number, "reason_code": reason_code})
    return findings


def _semantic_record_hash(line: str) -> str | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    if isinstance(record.get("messages"), list):
        semantic = {
            "messages": [
                {
                    "role": str(item.get("role") or "").strip().lower(),
                    "content": " ".join(str(item.get("content") or "").split()),
                }
                for item in record["messages"]
                if isinstance(item, dict)
            ]
        }
    else:
        semantic = {
            "instruction": " ".join(str(record.get("instruction") or "").split()),
            "input": " ".join(str(record.get("input") or "").split()),
            "output": " ".join(str(record.get("output") or "").split()),
        }
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _record_overlap_count(train_path: Path, validation_path: Path) -> int:
    train_hashes: set[str] = set()
    with train_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            digest = _semantic_record_hash(line)
            if digest:
                train_hashes.add(digest)
    overlaps: set[str] = set()
    with validation_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            digest = _semantic_record_hash(line)
            if digest and digest in train_hashes:
                overlaps.add(digest)
    return len(overlaps)


def _validation_reason_codes(
    train: dict[str, Any],
    validation: dict[str, Any] | None,
    pair_errors: list[str],
    pii_findings: list[dict[str, Any]],
    override: bool,
) -> list[str]:
    reasons = {str(item.get("type") or "validation_error") for item in train.get("errors") or []}
    if validation:
        reasons.update(str(item.get("type") or "validation_error") for item in validation.get("errors") or [])
    if pair_errors:
        reasons.add("train_validation_pair_invalid")
    if pii_findings and not override:
        reasons.add("pii_detected")
    if pii_findings and override:
        reasons.add("pii_override_applied")
    return sorted(reasons)


def _safe_validation_report(report: dict[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in report.items() if key != "dataset_path"}
    safe["secret_findings"] = [
        {
            "line": int(item.get("line") or 0),
            "pattern": str(item.get("pattern") or "secret_detected"),
        }
        for item in report.get("secret_findings") or []
        if isinstance(item, dict)
    ]
    return safe
