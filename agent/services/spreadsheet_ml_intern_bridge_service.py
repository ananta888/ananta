"""Adapter from governed spreadsheet datasets into the Hub ML-Intern control plane."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.spreadsheet_learning_service import SpreadsheetLearningService
from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy


class SpreadsheetMlInternBridgeService:
    """Composes existing catalog/bridge/control ports without duplicating training logic."""

    def __init__(
        self,
        *,
        learning: SpreadsheetLearningService,
        catalog: Any,
        split: Any,
        repository_bridge: Any,
        control: Any,
        strategy: SpreadsheetTrainingTaskFamilyStrategy | None = None,
    ) -> None:
        self._learning = learning
        self._catalog = catalog
        self._split = split
        self._bridge = repository_bridge
        self._control = control
        self._strategy = strategy or SpreadsheetTrainingTaskFamilyStrategy()

    def start_training(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        required = {
            "schema",
            "dataset_id",
            "mode",
            "backend",
            "base_model",
            "method",
            "hyperparameters",
            "live_confirmed",
            "risk_reason",
        }
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-training-command.v1":
            raise ValueError("spreadsheet_training_fields_invalid")
        mode = str(payload.get("mode") or "")
        if mode not in {"dry_run", "live"}:
            raise ValueError("spreadsheet_training_mode_invalid")
        dataset = self._learning.get_dataset(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=str(payload.get("dataset_id") or ""),
        )
        readiness = dict(dataset.get("readiness") or {})
        if not readiness.get("dry_run_ready") or (mode == "live" and not readiness.get("training_ready")):
            raise PermissionError("spreadsheet_dataset_not_ready")
        path = self._learning.dataset_path(
            tenant_id=tenant_id,
            principal_id=principal_id,
            dataset_id=str(dataset["dataset_id"]),
        )
        records = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(self._strategy.validate_record(json.loads(line)))
        catalog = self._catalog.create_from_records(
            tenant_id=tenant_id,
            principal_id=principal_id,
            records=records,
            name=f"Spreadsheet Studio {dataset['dataset_id']}",
            dataset_format="instruction",
            idempotency_key=f"spreadsheet-{dataset['dataset_digest']}",
        )
        validation_report = None
        catalog_summary = catalog
        if mode == "live":
            validation_ratio = max(0.05, min(0.5, 1.0 - float(dataset["split_percent"]["train"]) / 100.0))
            split_result = self._split.split(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=catalog["dataset_id"],
                validation_ratio=validation_ratio,
                seed=int(str(dataset["split_seed"].encode().hex())[:8], 16),
            )
            catalog_summary = split_result["dataset"]
            validation_report = self._catalog.validate_dataset(
                tenant_id=tenant_id,
                principal_id=principal_id,
                dataset_id=catalog["dataset_id"],
            )
            if not validation_report.get("ok"):
                raise PermissionError("spreadsheet_dataset_validation_failed")
        principal = MlInternTrainingPrincipal(tenant_id=tenant_id, subject=principal_id)
        projected = self._bridge.sync(
            principal,
            catalog_summary,
            validation_report=validation_report,
            metadata={
                "purpose": "spreadsheet_action_training",
                "privacy": "consented_masked",
            },
        )
        command = {
            "dataset_id": projected["id"],
            "job_type": "train_lora",
            "mode": mode,
            "backend": str(payload.get("backend") or ""),
            "base_model": str(payload.get("base_model") or ""),
            "method": str(payload.get("method") or ""),
            "hyperparameters": dict(payload.get("hyperparameters") or {}),
            "task_family": "spreadsheet_actions",
            "task_kinds": ["spreadsheet_actions"],
            "output_schema_digest": self._strategy.schema_digest,
            "serializer_digest": self._strategy.serializer_digest,
            "require_dataset_validation": mode == "live",
            "require_secret_scan": True,
        }
        if mode == "live":
            command.update(
                {
                    "live_confirmed": payload.get("live_confirmed"),
                    "risk_reason": payload.get("risk_reason"),
                }
            )
        job, replayed = self._control.create_job(
            principal,
            command,
            idempotency_key=idempotency_key,
        )
        return {
            "schema": "ananta.spreadsheet-training-admission.v1",
            "spreadsheet_dataset_id": dataset["dataset_id"],
            "ml_intern_dataset_id": projected["id"],
            "job": job,
            "replayed": replayed,
            "task_family": "spreadsheet_actions",
            "output_schema_digest": self._strategy.schema_digest,
            "serializer_digest": self._strategy.serializer_digest,
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetMlInternBridgeService"]
