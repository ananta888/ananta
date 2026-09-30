"""ML-Intern LoRA dataset step adapter and its Hub catalog ports.

``ml_intern_build_lora_dataset`` resolves or creates one canonical Hub
dataset.  The catalog and legacy-import adapters are the production
implementations of the narrow ports declared in
:mod:`agent.visual_process.step_adapters_ml_intern_support`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.step_adapters_ml_intern_support import (
    DatasetCatalogBuildFactory,
    LegacyDatasetImportFactory,
    MlInternVisualProcessAdapterError,
    _ml_intern_training_config,
    _ml_intern_training_principal,
)
from agent.visual_process.step_executor import StepAdapter, StepExecutionResult


class MlInternBuildLoraDatasetAdapter(StepAdapter):
    """Create or resolve one canonical Hub dataset without accepting server paths."""

    _LEGACY_PATH_FIELDS = frozenset(
        {
            "dataset_path",
            "datasetPath",
            "dataset_root",
            "datasetRoot",
            "source_paths",
            "sourcePaths",
            "output_path",
            "outputPath",
        }
    )
    _RECORD_KEYS = ("records", "training_examples", "examples", "dataset_records")

    def __init__(
        self,
        *,
        catalog_factory: DatasetCatalogBuildFactory | None = None,
        legacy_dataset_import_factory: LegacyDatasetImportFactory | None = None,
    ) -> None:
        self._catalog_factory = catalog_factory or (
            lambda config: MlInternVisualProcessDatasetCatalogAdapter(config)
        )
        self._legacy_dataset_import_factory = legacy_dataset_import_factory or (
            lambda config: MlInternLegacyDatasetImportAdapter(config)
        )

    @property
    def kind(self) -> str:
        return "ml_intern_build_lora_dataset"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal

        metadata = dict(step.metadata or {})
        warnings = self._legacy_warnings(metadata, artifacts)
        try:
            config = _ml_intern_training_config(context)
            principal = _ml_intern_training_principal(context, MlInternTrainingPrincipal)
            catalog = self._catalog_factory(config)
            dataset_id = self._dataset_id(metadata, artifacts)
            records = self._records(metadata, artifacts)
            source_mode = "catalog_reference"

            if dataset_id:
                projection = dict(catalog.get_dataset(principal, dataset_id))
            elif records is not None:
                source_mode = "bounded_upstream_records"
                projection = dict(
                    catalog.create_from_records(
                        principal,
                        records,
                        name=str(metadata.get("name") or metadata.get("dataset_name") or step.label)[:160],
                        dataset_format=str(metadata.get("format") or "instruction"),
                        validation_ratio=self._validation_ratio(metadata, config),
                        split_seed=self._split_seed(metadata, config),
                        idempotency_key=self._idempotency_key(
                            step=step,
                            context=context,
                            principal=principal,
                            metadata=metadata,
                            records=records,
                        ),
                        metadata=self._dataset_metadata(metadata),
                    )
                )
                dataset_id = str(projection.get("id") or "").strip()
            else:
                legacy_path = self._legacy_source_path(metadata, artifacts)
                if not legacy_path:
                    raise MlInternVisualProcessAdapterError(
                        "dataset_input_required",
                        "ml_intern_build_lora_dataset requires dataset_id or bounded upstream records",
                    )
                source_mode = "legacy_quarantine_import"
                dataset_id = self._legacy_dataset_import_factory(config).import_relative_path(principal, legacy_path)
                projection = dict(catalog.get_dataset(principal, dataset_id))

            if not dataset_id:
                dataset_id = str(projection.get("id") or "").strip()
            if not dataset_id:
                raise MlInternVisualProcessAdapterError(
                    "dataset_projection_invalid",
                    "Hub dataset projection did not return a canonical dataset_id",
                )
            return self._dataset_result(
                projection,
                dataset_id=dataset_id,
                source_mode=source_mode,
                warnings=warnings,
            )
        except Exception as exc:
            reason_code = str(getattr(exc, "reason_code", "ml_intern_dataset_catalog_vp_failed"))[:128]
            safe_error = str(exc)[:512] if hasattr(exc, "reason_code") else "Hub dataset catalog operation failed"
            return StepExecutionResult(
                status="failed",
                outputs={"dataset_status": "failed"},
                diagnostics={"reason_code": reason_code, "error": safe_error},
                warnings=warnings,
                backend_service="MlInternDatasetCatalogService + MlInternDatasetRepositoryBridgeService",
                executable=True,
                execution_reason=f"ml_intern_build_lora_dataset: {reason_code}",
            )

    @staticmethod
    def _dataset_id(metadata: Mapping[str, Any], artifacts: Mapping[str, Any]) -> str:
        return str(
            artifacts.get("dataset_id")
            or artifacts.get("datasetId")
            or metadata.get("dataset_id")
            or metadata.get("datasetId")
            or ""
        ).strip()

    @classmethod
    def _records(
        cls,
        metadata: Mapping[str, Any],
        artifacts: Mapping[str, Any],
    ) -> list[dict[str, Any]] | None:
        raw: Any = None
        found = False
        for source in (artifacts, metadata):
            for key in cls._RECORD_KEYS:
                if key in source:
                    if found:
                        raise MlInternVisualProcessAdapterError(
                            "dataset_records_ambiguous",
                            "provide exactly one bounded records artifact",
                        )
                    raw = source[key]
                    found = True
        if not found:
            return None
        if not isinstance(raw, list):
            raise MlInternVisualProcessAdapterError(
                "dataset_records_invalid",
                "bounded records artifact must be a JSON array",
            )
        if any(not isinstance(record, dict) for record in raw):
            raise MlInternVisualProcessAdapterError(
                "dataset_records_invalid",
                "every bounded dataset record must be a JSON object",
            )
        return [dict(record) for record in raw]

    @staticmethod
    def _legacy_source_path(metadata: Mapping[str, Any], artifacts: Mapping[str, Any]) -> str:
        direct = (
            artifacts.get("dataset_path")
            or artifacts.get("datasetPath")
            or metadata.get("dataset_path")
            or metadata.get("datasetPath")
        )
        source_paths = (
            artifacts.get("source_paths")
            or artifacts.get("sourcePaths")
            or metadata.get("source_paths")
            or metadata.get("sourcePaths")
        )
        values: list[str] = []
        if direct is not None and str(direct).strip():
            values.append(str(direct).strip())
        if source_paths is not None:
            parsed: Any = source_paths
            if isinstance(source_paths, str) and source_paths.strip().startswith("["):
                try:
                    parsed = json.loads(source_paths)
                except json.JSONDecodeError as exc:
                    raise MlInternVisualProcessAdapterError(
                        "legacy_dataset_sources_invalid",
                        "deprecated source_paths must be one relative path",
                    ) from exc
            candidates = parsed if isinstance(parsed, list) else [parsed]
            values.extend(str(value).strip() for value in candidates if str(value or "").strip())
        unique = list(dict.fromkeys(values))
        if len(unique) > 1:
            raise MlInternVisualProcessAdapterError(
                "legacy_dataset_sources_ambiguous",
                "deprecated source_paths supports exactly one quarantined source",
            )
        return unique[0] if unique else ""

    @staticmethod
    def _validation_ratio(metadata: Mapping[str, Any], config: Mapping[str, Any]) -> float:
        value = metadata.get("validation_ratio", metadata.get("validationRatio", config.get("validation_ratio", 0.1)))
        try:
            ratio = float(value)
        except (TypeError, ValueError) as exc:
            raise MlInternVisualProcessAdapterError(
                "validation_ratio_invalid",
                "validation_ratio must be between 0.05 and 0.5",
            ) from exc
        if not 0.05 <= ratio <= 0.5:
            raise MlInternVisualProcessAdapterError(
                "validation_ratio_invalid",
                "validation_ratio must be between 0.05 and 0.5",
            )
        return ratio

    @staticmethod
    def _split_seed(metadata: Mapping[str, Any], config: Mapping[str, Any]) -> int:
        value = metadata.get("split_seed", metadata.get("splitSeed", config.get("split_seed", 42)))
        try:
            seed = int(value)
        except (TypeError, ValueError) as exc:
            raise MlInternVisualProcessAdapterError(
                "split_seed_invalid",
                "split_seed must be an integer between 0 and 2147483647",
            ) from exc
        if not 0 <= seed <= 2**31 - 1:
            raise MlInternVisualProcessAdapterError(
                "split_seed_invalid",
                "split_seed must be an integer between 0 and 2147483647",
            )
        return seed

    @staticmethod
    def _dataset_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "purpose": str(metadata.get("purpose") or "")[:512],
            "license": str(metadata.get("license") or "")[:512],
            "privacy": str(metadata.get("privacy") or "private")[:64],
        }

    @staticmethod
    def _idempotency_key(
        *,
        step: VisualProcessStep,
        context: Mapping[str, Any],
        principal: Any,
        metadata: Mapping[str, Any],
        records: list[dict[str, Any]],
    ) -> str:
        explicit = str(
            metadata.get("idempotency_key") or context.get("idempotency_key") or ""
        ).strip()
        if explicit:
            return explicit
        identity = {
            "visual_process_id": context.get("visual_process_id") or context.get("graph_id"),
            "run_id": context.get("visual_process_run_id") or context.get("run_id") or context.get("execution_id"),
            "step_id": step.id,
            "tenant_id": principal.tenant_id,
            "subject": principal.subject,
            "records": records,
        }
        try:
            canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise MlInternVisualProcessAdapterError(
                "dataset_records_invalid",
                "bounded dataset records must be canonical JSON",
            ) from exc
        return f"vp-dataset-{hashlib.sha256(canonical.encode()).hexdigest()}"

    @classmethod
    def _dataset_result(
        cls,
        projection: Mapping[str, Any],
        *,
        dataset_id: str,
        source_mode: str,
        warnings: list[str],
    ) -> StepExecutionResult:
        model_training_url = "/model-training"
        dataset_url = f"{model_training_url}?tab=datasets&dataset_id={dataset_id}"
        dataset_status = str(projection.get("status") or projection.get("validation_status") or "unknown")
        return StepExecutionResult(
            status="success",
            outputs={
                "dataset_build_result": dict(projection),
                "dataset_id": dataset_id,
                "dataset_status": dataset_status,
                "model_training_url": model_training_url,
                "dataset_url": dataset_url,
                "links": {"model_training": model_training_url, "dataset": dataset_url},
            },
            diagnostics={
                "source_mode": source_mode,
                "record_count": int(projection.get("record_count") or 0),
                "train_record_count": int(projection.get("train_record_count") or 0),
                "validation_record_count": int(projection.get("validation_record_count") or 0),
                "validation_status": str(projection.get("validation_status") or "unknown"),
            },
            warnings=warnings,
            backend_service="MlInternDatasetCatalogService + MlInternDatasetRepositoryBridgeService",
            executable=True,
            execution_reason=f"vp_adapter: Hub dataset catalog status={dataset_status}",
        )

    @classmethod
    def _legacy_warnings(cls, metadata: Mapping[str, Any], artifacts: Mapping[str, Any]) -> list[str]:
        present = sorted(key for key in cls._LEGACY_PATH_FIELDS if key in metadata or key in artifacts)
        if not present:
            return []
        return [
            "Deprecated VP dataset path fields detected: "
            + ", ".join(present)
            + ". They are read-only migration inputs; Hub roots and output paths are ignored."
        ]


class MlInternVisualProcessDatasetCatalogAdapter:
    """Narrow VP port over the same bounded catalog/repository path as the API."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        from agent.repositories.ml_intern_training import get_ml_intern_training_repository
        from agent.services.ml_intern_artifact_security_service import (
            ArtifactSecurityPolicy,
            MlInternArtifactSecurityService,
        )
        from agent.services.ml_intern_dataset_catalog_service import MlInternDatasetCatalogService
        from agent.services.ml_intern_dataset_repository_bridge_service import (
            MlInternDatasetRepositoryBridgeService,
        )
        from agent.services.ml_intern_dataset_split_service import MlInternDatasetSplitService
        from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config

        raw_config = dict(config)
        self._config = normalize_ml_intern_training_config(raw_config)
        dataset_root = Path(self._config["dataset_root"])
        maximum = int(self._config["max_dataset_bytes"])
        policy = ArtifactSecurityPolicy(
            max_file_bytes=maximum,
            max_request_bytes=maximum + 512 * 1024,
            max_tenant_bytes=maximum * 20,
            max_archive_uncompressed_bytes=maximum * 2,
        )
        catalog_root = Path(raw_config.get("dataset_catalog_root") or dataset_root / "catalog")
        self._catalog = MlInternDatasetCatalogService(
            storage_root=catalog_root,
            security=MlInternArtifactSecurityService(storage_root=catalog_root, policy=policy),
        )
        self._split = MlInternDatasetSplitService(self._catalog)
        self._repository = get_ml_intern_training_repository()
        self._bridge = MlInternDatasetRepositoryBridgeService(
            execution_root=dataset_root,
            catalog=self._catalog,
            repository=self._repository,
            max_dataset_bytes=maximum,
        )

    def create_from_records(
        self,
        principal: Any,
        records: list[dict[str, Any]],
        *,
        name: str,
        dataset_format: str,
        validation_ratio: float,
        split_seed: int,
        idempotency_key: str,
        metadata: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        summary = self._catalog.create_from_records(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            records=records,
            name=name,
            dataset_format=dataset_format,
            idempotency_key=idempotency_key,
        )
        return self._split_validate_and_project(
            principal,
            summary,
            validation_ratio=validation_ratio,
            split_seed=split_seed,
            metadata=metadata,
        )

    def create_from_quarantined_upload(
        self,
        principal: Any,
        *,
        stream: Any,
        filename: str,
        media_type: str,
        name: str,
        idempotency_key: str,
        declared_size: int,
        expected_sha256: str,
    ) -> Mapping[str, Any]:
        summary = self._catalog.create_from_upload(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            stream=stream,
            filename=filename,
            media_type=media_type,
            name=name,
            dataset_format="instruction",
            idempotency_key=idempotency_key,
            declared_size=declared_size,
            expected_sha256=expected_sha256,
        )
        return self._split_validate_and_project(
            principal,
            summary,
            validation_ratio=float(self._config["validation_ratio"]),
            split_seed=int(self._config["split_seed"]),
            metadata={"purpose": "VP legacy quarantine import", "privacy": "private"},
        )

    def get_dataset(self, principal: Any, dataset_id: str) -> Mapping[str, Any]:
        from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService

        dataset = self._repository.get_dataset(principal, str(dataset_id or "").strip())
        if dataset is None:
            raise MlInternVisualProcessAdapterError(
                "dataset_not_found",
                "model-training dataset does not exist for this principal",
            )
        return MlInternTrainingReadModelService.dataset(dataset)

    def _split_validate_and_project(
        self,
        principal: Any,
        summary: Mapping[str, Any],
        *,
        validation_ratio: float,
        split_seed: int,
        metadata: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        catalog_id = str(summary.get("dataset_id") or "")
        if not catalog_id:
            raise MlInternVisualProcessAdapterError(
                "dataset_catalog_response_invalid",
                "Hub dataset catalog did not return a dataset identifier",
            )
        # Mirror the API lifecycle: make ingress visible, then project the
        # immutable split and validation state onto the same repository row.
        self._bridge.sync(principal, summary, metadata=metadata)
        current = dict(summary)
        if "validation" not in dict(current.get("partitions") or {}):
            split = self._split.split(
                tenant_id=principal.tenant_id,
                principal_id=principal.subject,
                dataset_id=catalog_id,
                validation_ratio=validation_ratio,
                seed=split_seed,
            )
            current = dict(split["dataset"])
        report = self._catalog.validate_dataset(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
        )
        current = self._catalog.get_dataset(
            tenant_id=principal.tenant_id,
            principal_id=principal.subject,
            dataset_id=catalog_id,
        )
        return self._bridge.sync(
            principal,
            current,
            validation_report=report,
            metadata=metadata,
        )


class MlInternLegacyDatasetImportAdapter:
    """Quarantine a deprecated relative dataset path into the v2 catalog.

    The legacy path is never persisted in a job request.  It is resolved below
    the Hub-owned dataset root, copied through the bounded ingress service,
    split, validated and projected into the same SQL repository used by the
    model-training API.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        from agent.services.ml_intern_artifact_security_service import (
            ArtifactSecurityPolicy,
            MlInternArtifactSecurityService,
        )
        from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config

        self._config = normalize_ml_intern_training_config(dict(config))
        self._dataset_root = Path(self._config["dataset_root"])
        maximum = int(self._config["max_dataset_bytes"])
        policy = ArtifactSecurityPolicy(
            max_file_bytes=maximum,
            max_request_bytes=maximum + 512 * 1024,
            max_tenant_bytes=maximum * 20,
            max_archive_uncompressed_bytes=maximum * 2,
        )
        self._source_store = MlInternArtifactSecurityService(
            storage_root=self._dataset_root,
            policy=policy,
        )
        self._catalog_adapter = MlInternVisualProcessDatasetCatalogAdapter(config)

    def import_relative_path(self, principal: Any, relative_path: str) -> str:
        normalized_path = self._relative_dataset_path(relative_path)
        source = self._source_store.resolve_relative(normalized_path, must_exist=True)
        if not source.is_file() or source.suffix.lower() not in {".json", ".jsonl"}:
            raise MlInternVisualProcessAdapterError(
                "legacy_dataset_type_invalid",
                "legacy dataset_path must reference a JSON or JSONL file",
            )
        digest = self._sha256(source)
        media_type = "application/x-ndjson" if source.suffix.lower() == ".jsonl" else "application/json"
        with source.open("rb") as stream:
            projection = self._catalog_adapter.create_from_quarantined_upload(
                principal,
                stream=stream,
                filename=source.name,
                media_type=media_type,
                name=f"VP legacy import {source.stem}"[:160],
                idempotency_key=f"vp-legacy-{digest}",
                declared_size=source.stat().st_size,
                expected_sha256=digest,
            )
        dataset_id = str(projection.get("id") or "")
        if not dataset_id:
            raise MlInternVisualProcessAdapterError(
                "legacy_dataset_projection_failed",
                "legacy dataset could not be projected into the training catalog",
            )
        return dataset_id

    @staticmethod
    def _relative_dataset_path(value: str) -> str:
        raw = str(value or "").strip()
        if not raw or "\x00" in raw or "\\" in raw:
            raise MlInternVisualProcessAdapterError(
                "legacy_dataset_path_invalid",
                "legacy dataset_path must be a clean relative path",
            )
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise MlInternVisualProcessAdapterError(
                "legacy_dataset_path_invalid",
                "legacy dataset_path must be a clean relative path",
            )
        return path.as_posix()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
