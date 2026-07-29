"""Hub composition for tenant-owned dataset snapshots and queued recipes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.unsloth_data_recipe_adapter import (
    DataRecipeManifest,
    DataRecipeRequest,
    DatasetSnapshot,
    UnslothDataRecipeAdapter,
)
from agent.services.unsloth_task_port import HubTaskSubmissionPort


class RepositoryDatasetSnapshotAdapter:
    """Projects metadata only; it never opens a dataset partition."""

    def __init__(
        self,
        *,
        repository: Any,
        principal: MlInternTrainingPrincipal,
        dataset_root: str | Path,
    ) -> None:
        self._repository = repository
        self._principal = principal
        self._dataset_root = Path(dataset_root).resolve()

    def get_snapshot(self, *, tenant_id: str, dataset_id: str) -> DatasetSnapshot | None:
        if tenant_id != self._principal.tenant_id:
            return None
        dataset = self._repository.get_dataset(self._principal, dataset_id)
        if dataset is None:
            return None
        report = dict(getattr(dataset, "validation_report", None) or {})
        metadata = dict(getattr(dataset, "dataset_metadata", None) or {})
        partition_hashes = dict(metadata.get("partition_hashes") or {})
        raw_storage_ref = str(
            getattr(dataset, "train_storage_ref", "") or ""
        ).strip()
        if not raw_storage_ref:
            return None
        try:
            storage_ref = Path(raw_storage_ref).resolve(strict=True)
            dataset_ref = storage_ref.relative_to(
                self._dataset_root
            ).as_posix()
        except (OSError, ValueError):
            return None
        if (
            not storage_ref.is_file()
            or storage_ref.is_symlink()
            or dataset_ref in {"", "."}
        ):
            return None
        validation_ok = report.get("ok") is True
        license_state = str(metadata.get("license_status") or "pending").strip().lower()
        pii_count = int(
            report.get("pii_finding_count")
            or len(report.get("pii_findings") or ())
            or 0
        )
        secret_count = int(
            getattr(dataset, "secret_finding_count", 0)
            or report.get("secret_finding_count")
            or 0
        )
        approved = (
            str(getattr(dataset, "status", "") or "") == "validated"
            and validation_ok
            and license_state == "approved"
        )
        return DatasetSnapshot(
            dataset_id=str(dataset.id),
            tenant_id=str(dataset.tenant_id),
            dataset_hash=str(dataset.content_sha256),
            dataset_ref=dataset_ref,
            dataset_partition_sha256=str(
                partition_hashes.get("train") or ""
            ),
            state="approved" if approved else str(dataset.status),
            secret_scan_state="passed" if validation_ok and secret_count == 0 else "failed",
            pii_state="clear" if validation_ok and pii_count == 0 else "review_required",
            license_state=license_state,
            row_count=int(
                getattr(dataset, "train_record_count", 0) or 0
            ),
        )


@dataclass(frozen=True)
class DataRecipeSubmission:
    task_id: str
    manifest: DataRecipeManifest


class UnslothDataRecipeSubmissionService:
    def __init__(
        self,
        *,
        adapter: UnslothDataRecipeAdapter,
        tasks: HubTaskSubmissionPort,
    ) -> None:
        self._adapter = adapter
        self._tasks = tasks

    def submit(self, request: DataRecipeRequest) -> DataRecipeSubmission:
        manifest = self._adapter.build(request)
        payload = {
            "schema": "ananta.unsloth-data-recipe-task.v1",
            "manifest": json.loads(manifest.canonical_json()),
        }
        task_id = self._tasks.submit(
            task_type="ml.dataset.recipe.materialize",
            tenant_id=request.tenant_id,
            payload=payload,
            idempotency_key=manifest.recipe_id,
        )
        return DataRecipeSubmission(task_id=task_id, manifest=manifest)
