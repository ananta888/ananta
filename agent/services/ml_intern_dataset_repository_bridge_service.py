"""Bridge the curated filesystem catalog into durable Hub training metadata.

The catalog remains responsible for content preparation and validation.  This
service stages immutable train/validation files into the worker transfer root
and mirrors only bounded metadata into the SQL control plane.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from agent.db_models import MlInternDatasetDB
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_dataset_catalog_service import MlInternDatasetCatalogService
from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


class MlInternDatasetRepositoryBridgeService:
    """Stage catalog partitions and maintain their tenant-scoped SQL projection."""

    def __init__(
        self,
        *,
        execution_root: str | Path,
        catalog: MlInternDatasetCatalogService,
        repository: MlInternTrainingRepository,
        max_dataset_bytes: int,
    ) -> None:
        self._catalog = catalog
        self._repository = repository
        self._store = MlInternArtifactSecurityService(
            storage_root=execution_root,
            policy=ArtifactSecurityPolicy(
                max_file_bytes=max_dataset_bytes,
                max_request_bytes=max_dataset_bytes * 2,
                max_tenant_bytes=max_dataset_bytes * 20,
                max_archive_uncompressed_bytes=max_dataset_bytes,
                max_records=catalog.max_records,
            ),
        )
        self._read_models = MlInternTrainingReadModelService()

    def sync(
        self,
        principal: MlInternTrainingPrincipal,
        catalog_summary: Mapping[str, Any],
        *,
        validation_report: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        catalog_id = str(catalog_summary.get("dataset_id") or "")
        revision = max(1, int(catalog_summary.get("revision") or 1))
        tenant_key = hashlib.sha256(principal.tenant_id.encode("utf-8")).hexdigest()
        base = f"tenants/{tenant_key}/datasets/{catalog_id}/revision-{revision}"
        train = self._stage_partition(principal, catalog_id, "train", f"{base}/train.jsonl")
        validation = None
        partitions = catalog_summary.get("partitions")
        if isinstance(partitions, Mapping) and "validation" in partitions:
            validation = self._stage_partition(
                principal,
                catalog_id,
                "validation",
                f"{base}/validation.jsonl",
                request_bytes_used=train["size_bytes"],
            )

        existing = self._find_projection(principal, catalog_id)
        report = dict(validation_report or {})
        if not report:
            catalog_validation = catalog_summary.get("validation")
            if isinstance(catalog_validation, Mapping):
                report = {
                    "ok": bool(catalog_validation.get("trainable", False)),
                    "status": catalog_validation.get("status"),
                    **dict(catalog_validation.get("summary") or {}),
                }
        values = {
            "name": str(catalog_summary.get("name") or catalog_id)[:160],
            "status": str(catalog_summary.get("status") or "uploaded")[:64],
            "format_type": str(catalog_summary.get("format_type") or "instruction")[:32],
            "content_sha256": str(catalog_summary.get("sha256") or train["sha256"]),
            "size_bytes": int(catalog_summary.get("size_bytes") or train["size_bytes"]),
            "record_count": int(catalog_summary.get("record_count") or train["record_count"]),
            "train_record_count": int(train["record_count"]),
            "validation_record_count": int(validation["record_count"] if validation else 0),
            "rejected_record_count": int(catalog_summary.get("rejected_record_count") or 0),
            "duplicate_record_count": int(catalog_summary.get("duplicate_count") or 0),
            "secret_finding_count": int(
                report.get("secret_finding_count") or len(report.get("secret_findings") or []) or 0
            ),
            "storage_ref": train["path"],
            "train_storage_ref": train["path"],
            "validation_storage_ref": validation["path"] if validation else None,
            "split_manifest": dict(catalog_summary.get("split") or {}),
            "validation_report": report,
            "dataset_metadata": {
                **(dict(existing.dataset_metadata or {}) if existing is not None else {}),
                "catalog_dataset_id": catalog_id,
                "catalog_revision": revision,
                "partition_hashes": {
                    "train": train["sha256"],
                    **({"validation": validation["sha256"]} if validation else {}),
                },
                **{
                    key: str(metadata[key])[:512]
                    for key in ("purpose", "license", "privacy")
                    if metadata is not None and metadata.get(key) is not None
                },
            },
        }
        if existing is None:
            projected, _ = self._repository.create_dataset(
                MlInternDatasetDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            projected = self._repository.save_dataset(existing, expected_version=existing.version)
        return self._read_models.dataset(projected)

    def catalog_dataset_id(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> str:
        dataset = self._repository.get_dataset(principal, dataset_id)
        if dataset is None:
            raise KeyError(dataset_id)
        catalog_id = str((dataset.dataset_metadata or {}).get("catalog_dataset_id") or "")
        if not catalog_id:
            raise KeyError(dataset_id)
        return catalog_id

    def _find_projection(
        self,
        principal: MlInternTrainingPrincipal,
        catalog_dataset_id: str,
    ) -> MlInternDatasetDB | None:
        offset = 0
        while offset < 10_000:
            page = self._repository.list_datasets(principal, limit=200, offset=offset)
            for dataset in page:
                if (dataset.dataset_metadata or {}).get("catalog_dataset_id") == catalog_dataset_id:
                    return dataset
            if len(page) < 200:
                break
            offset += len(page)
        return None

    def _stage_partition(
        self,
        principal: MlInternTrainingPrincipal,
        catalog_id: str,
        partition: str,
        destination: str,
        *,
        request_bytes_used: int = 0,
    ) -> dict[str, Any]:
        descriptor = self._catalog.partition_descriptor(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
            partition=partition,
        )
        with self._catalog.open_partition(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
            partition=partition,
        ) as source:
            binary = getattr(source, "buffer", None)
            if binary is None:
                raise RuntimeError("catalog partition does not expose a binary stream")
            stored = self._store.store_upload(
                binary,
                destination_relative=destination,
                filename=f"{partition}.jsonl",
                media_type="application/x-ndjson",
                allowed_extensions={".jsonl"},
                allowed_media_types={"application/x-ndjson"},
                content_kind="jsonl",
                declared_size=int(descriptor["size_bytes"]),
                expected_sha256=str(descriptor["sha256"]),
                request_bytes_used=request_bytes_used,
                overwrite=True,
            )
        return {
            "path": str(self._store.resolve_relative(stored.relative_path, must_exist=True)),
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "record_count": int(descriptor["record_count"]),
        }
