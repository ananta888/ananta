"""VP-native step adapters (VPEXEC-002, VPEXEC-003).

These adapters run in the hub process.  A Hub adapter may materialize a
Hub-owned task that is subsequently delegated to a worker; it never creates a
second orchestration loop.  Registered by get_step_executor() on first use.

Adapters implemented here:
  query_rewrite       — rewrite_query() synonym expansion
  rerank              — Reranker token-overlap boost
  embed_api           — HashEmbeddingProvider / OpenAICompatibleEmbeddingProvider
  sign_rotation       — DeterministicSignRotation (TQ-011)
  turboquant_mse      — TurboQuantMseEncoder (TQ-012, experimental)
  workspace_snapshot  — WorkspaceDiffService.take_before_snapshot()
  workspace_diff      — WorkspaceDiffService.compute_diff() + synthesize_manifest()
  ml_intern_build_lora_dataset — Hub Dataset Catalog/Repository ingestion
  ml_intern_train_lora — MlInternTrainingControlService.create_job

CodeCompass (codecompass_*), Evolution (evolution_*), and domain_cluster
have implementation_state=registered_only — no adapter here, dry-run marks
them not_executable until dedicated adapters are built.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.step_executor import StepAdapter, StepExecutionResult


_BUILTIN_TOKEN_OVERLAP_RERANKER_DIGEST = "sha256:" + hashlib.sha256(
    b"ananta.visual-process.token-overlap-reranker.v1"
).hexdigest()


class _MlInternTrainingControlPort(Protocol):
    def create_job(
        self,
        principal: Any,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]: ...


class _LegacyDatasetImportPort(Protocol):
    def import_relative_path(self, principal: Any, relative_path: str) -> str: ...


class _DatasetCatalogBuildPort(Protocol):
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
    ) -> Mapping[str, Any]: ...

    def get_dataset(self, principal: Any, dataset_id: str) -> Mapping[str, Any]: ...


ControlFactory = Callable[[Mapping[str, Any]], _MlInternTrainingControlPort]
LegacyDatasetImportFactory = Callable[[Mapping[str, Any]], _LegacyDatasetImportPort]
DatasetCatalogBuildFactory = Callable[[Mapping[str, Any]], _DatasetCatalogBuildPort]


class MlInternVisualProcessAdapterError(ValueError):
    """Stable, content-free VP adapter rejection."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _ml_intern_training_config(context: Mapping[str, Any]) -> dict[str, Any]:
    """Merge trusted Hub configuration with an explicit execution context."""

    config: dict[str, Any] = {}
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            agent_config = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
            config.update(dict(agent_config.get("ml_intern_training") or {}))
            if "lora_runtime" in agent_config:
                config["lora_runtime"] = dict(agent_config.get("lora_runtime") or {})
    except RuntimeError:
        pass
    context_config = context.get("ml_intern_training")
    if isinstance(context_config, Mapping):
        config.update(dict(context_config))
    return config


def _ml_intern_training_principal(context: Mapping[str, Any], principal_type: type[Any]) -> Any:
    """Resolve the tenant-scoped principal shared by VP training adapters."""

    identity: Any = context.get("ml_intern_training_principal") or context.get("principal") or {}
    if isinstance(identity, Mapping):
        subject = str(
            identity.get("subject")
            or identity.get("sub")
            or identity.get("username")
            or context.get("subject")
            or "hub-admin"
        ).strip()
        tenant = str(
            identity.get("tenant_id") or identity.get("tenant") or context.get("tenant_id") or subject
        ).strip()
    else:
        subject = str(getattr(identity, "subject", None) or context.get("subject") or "hub-admin").strip()
        tenant = str(getattr(identity, "tenant_id", None) or context.get("tenant_id") or subject).strip()
    return principal_type(tenant_id=tenant, subject=subject)


# ── Query rewrite ─────────────────────────────────────────────────────────────

class QueryRewriteAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "query_rewrite"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.query_rewrite import rewrite_query
        query = str(artifacts.get("query") or step.metadata.get("query") or "")
        result = rewrite_query(query)
        return StepExecutionResult(
            status="success",
            outputs=result,
            backend_service="rewrite_query",
            executable=True,
            execution_reason="vp_adapter: synonym expansion (deterministic, no LLM, no network)",
        )


# ── ML-Intern LoRA dataset build + training ──────────────────────────────────

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


class MlInternTrainLoraAdapter(StepAdapter):
    """Materialize a VP LoRA step as the canonical Hub-owned async job."""

    _TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
    _FAILED_STATUSES = frozenset({"cancelled", "failed", "interrupted"})
    _TRAINING_PROFILES = frozenset({"rtx3080-safe", "generic-safe", "none"})
    _HYPERPARAMETER_KEYS = (
        "batch_size",
        "max_seq_length",
        "max_sequence_length",
        "gradient_accumulation_steps",
        "learning_rate",
        "lora_rank",
        "lora_alpha",
        "lora_dropout",
        "load_in_4bit",
        "quantization",
        "max_steps",
        "num_train_epochs",
        "target_modules",
        "evaluation_steps",
        "early_stopping_patience",
        "seed",
    )
    _LEGACY_PATH_FIELDS = frozenset(
        {"dataset_path", "datasetPath", "dataset_root", "datasetRoot", "artifact_root", "artifactRoot"}
    )

    def __init__(
        self,
        *,
        control_factory: ControlFactory | None = None,
        legacy_dataset_import_factory: LegacyDatasetImportFactory | None = None,
    ) -> None:
        self._control_factory = control_factory or self._default_control_factory
        self._legacy_dataset_import_factory = legacy_dataset_import_factory or (
            lambda config: MlInternLegacyDatasetImportAdapter(config)
        )

    @property
    def kind(self) -> str:
        return "ml_intern_train_lora"

    def execute(
        self,
        step: VisualProcessStep,
        artifacts: dict[str, Any],
        context: dict[str, Any],
    ) -> StepExecutionResult:
        from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal

        metadata = dict(step.metadata or {})
        warnings = self._legacy_warnings(metadata, artifacts)
        try:
            config = _ml_intern_training_config(context)
            principal = _ml_intern_training_principal(context, MlInternTrainingPrincipal)
            dataset_id = self._dataset_id(metadata, artifacts)
            legacy_path = self._legacy_dataset_path(metadata, artifacts)
            legacy_mode = not dataset_id and bool(legacy_path)
            if legacy_mode and self._requested_mode(metadata, artifacts, config) == "live":
                raise MlInternVisualProcessAdapterError(
                    "legacy_dataset_live_training_forbidden",
                    "migrate the quarantined dataset into the Hub catalog before starting a live run",
                )
            if legacy_mode:
                dataset_id = self._legacy_dataset_import_factory(config).import_relative_path(principal, legacy_path)
            if not dataset_id:
                raise MlInternVisualProcessAdapterError(
                    "dataset_id_required",
                    "ml_intern_train_lora requires a model-training dataset_id",
                )
            profile_id = self._training_profile(metadata, artifacts, config, legacy_mode=legacy_mode)
            payload = self._job_payload(
                metadata,
                artifacts,
                config,
                dataset_id=dataset_id,
                profile_id=profile_id,
            )
            idempotency_key = self._idempotency_key(
                step=step,
                context=context,
                metadata=metadata,
                artifacts=artifacts,
                principal=principal,
                payload=payload,
            )
            job, replayed = self._control_factory(config).create_job(
                principal,
                payload,
                idempotency_key=idempotency_key,
            )
            return self._job_result(
                job, dataset_id=dataset_id, profile_id=profile_id, replayed=replayed, warnings=warnings
            )
        except Exception as exc:
            reason_code = str(getattr(exc, "reason_code", "ml_intern_training_vp_failed"))[:128]
            training_status = "disabled" if reason_code == "training_disabled" else "failed"
            return StepExecutionResult(
                status="failed",
                outputs={"training_status": training_status},
                diagnostics={"reason_code": reason_code, "error": str(exc)[:512]},
                warnings=warnings,
                backend_service="MlInternTrainingControlService.create_job",
                executable=True,
                execution_reason=f"ml_intern_train_lora: {reason_code}",
            )

    @staticmethod
    def _default_control_factory(config: Mapping[str, Any]) -> _MlInternTrainingControlPort:
        from agent.services.ml_intern_training_control_service import get_ml_intern_training_control_service

        return get_ml_intern_training_control_service(config)

    @staticmethod
    def _dataset_id(metadata: Mapping[str, Any], artifacts: Mapping[str, Any]) -> str:
        return str(
            artifacts.get("dataset_id")
            or artifacts.get("datasetId")
            or metadata.get("dataset_id")
            or metadata.get("datasetId")
            or ""
        ).strip()

    @staticmethod
    def _legacy_dataset_path(metadata: Mapping[str, Any], artifacts: Mapping[str, Any]) -> str:
        return str(
            artifacts.get("dataset_path")
            or artifacts.get("datasetPath")
            or metadata.get("dataset_path")
            or metadata.get("datasetPath")
            or ""
        ).strip()

    @classmethod
    def _requested_mode(
        cls,
        metadata: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> str:
        normalized = cls._normalized_config(config)
        return str(
            artifacts.get("mode") or metadata.get("mode") or normalized["mode"]
        ).strip().lower()

    def _training_profile(
        self,
        metadata: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        config: Mapping[str, Any],
        *,
        legacy_mode: bool,
    ) -> str:
        profile = str(
            artifacts.get("training_profile_id")
            or artifacts.get("trainingProfileId")
            or artifacts.get("training_profile")
            or metadata.get("training_profile_id")
            or metadata.get("trainingProfileId")
            or metadata.get("training_profile")
            or metadata.get("trainingProfile")
            or artifacts.get("gpu_profile")
            or metadata.get("gpu_profile")
            or ""
        ).strip().lower()
        if not profile and legacy_mode:
            profile = str(config.get("gpu_profile") or "rtx3080-safe").strip().lower()
        if not profile:
            raise MlInternVisualProcessAdapterError(
                "training_profile_required",
                "ml_intern_train_lora requires training_profile_id",
            )
        if profile not in self._TRAINING_PROFILES:
            raise MlInternVisualProcessAdapterError(
                "training_profile_invalid",
                "training_profile_id is not an available bounded GPU profile",
            )
        return profile

    def _job_payload(
        self,
        metadata: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        config: Mapping[str, Any],
        *,
        dataset_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        base_model = str(
            artifacts.get("base_model")
            or artifacts.get("base_model_id")
            or metadata.get("base_model")
            or metadata.get("baseModel")
            or metadata.get("base_model_id")
            or ""
        ).strip()
        if not base_model:
            raise MlInternVisualProcessAdapterError(
                "base_model_required",
                "ml_intern_train_lora requires base_model",
            )
        from agent.services.ml_intern_training_contract import require_identifier

        normalized = self._normalized_config(config)
        hyperparameters: dict[str, Any] = {}
        for source in (metadata.get("hyperparameters"), artifacts.get("hyperparameters")):
            if isinstance(source, Mapping):
                hyperparameters.update(dict(source))
        for key in self._HYPERPARAMETER_KEYS:
            if key in artifacts:
                hyperparameters[key] = artifacts[key]
            elif key in metadata:
                hyperparameters[key] = metadata[key]
        output_name = require_identifier(
            "output_name",
            artifacts.get("output_name")
            or metadata.get("output_name")
            or metadata.get("outputName")
            or metadata.get("output_dir")
            or metadata.get("outputDir")
            or "vp-lora-adapter",
        )
        payload: dict[str, Any] = {
            "dataset_id": dataset_id,
            "job_type": "train_lora",
            "mode": str(artifacts.get("mode") or metadata.get("mode") or normalized["mode"]).strip().lower(),
            "backend": str(artifacts.get("backend") or metadata.get("backend") or normalized["backend"])
            .strip()
            .lower(),
            "base_model": base_model,
            "method": str(artifacts.get("method") or metadata.get("method") or "qlora").strip().lower(),
            "gpu_profile": profile_id,
            "output_name": output_name,
            "hyperparameters": hyperparameters,
            "require_dataset_validation": bool(
                metadata.get("require_dataset_validation", normalized["require_dataset_validation"])
            ),
            "require_secret_scan": bool(metadata.get("require_secret_scan", normalized["require_secret_scan"])),
        }
        for key in ("approval_id", "risk_reason", "live_confirmed"):
            if key in artifacts:
                payload[key] = artifacts[key]
            elif key in metadata:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _normalized_config(config: Mapping[str, Any]) -> dict[str, Any]:
        from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config

        return normalize_ml_intern_training_config(dict(config))

    @classmethod
    def _idempotency_key(
        cls,
        *,
        step: VisualProcessStep,
        context: Mapping[str, Any],
        metadata: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        principal: Any,
        payload: Mapping[str, Any],
    ) -> str:
        explicit = str(
            artifacts.get("idempotency_key") or metadata.get("idempotency_key") or context.get("idempotency_key") or ""
        ).strip()
        if explicit:
            return explicit
        execution_identity = {
            "visual_process_id": context.get("visual_process_id") or context.get("graph_id"),
            "run_id": context.get("visual_process_run_id") or context.get("run_id") or context.get("execution_id"),
            "task_id": context.get("task_id"),
            "step_id": step.id,
            "tenant_id": principal.tenant_id,
            "subject": principal.subject,
            "payload": dict(payload),
        }
        try:
            canonical = json.dumps(execution_identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise MlInternVisualProcessAdapterError(
                "job_payload_invalid",
                "visual-process training inputs are not canonical JSON",
            ) from exc
        return f"vp-lora-{hashlib.sha256(canonical.encode()).hexdigest()}"

    @classmethod
    def _job_result(
        cls,
        job: Mapping[str, Any],
        *,
        dataset_id: str,
        profile_id: str,
        replayed: bool,
        warnings: list[str],
    ) -> StepExecutionResult:
        job_id = str(job.get("id") or job.get("job_id") or "").strip()
        if not job_id:
            raise MlInternVisualProcessAdapterError(
                "training_job_response_invalid",
                "Hub training control did not return a job ID",
            )
        status = str(job.get("status") or "queued")
        phase = str(job.get("phase") or status)
        terminal = status in cls._TERMINAL_STATUSES
        step_status = "failed" if status in cls._FAILED_STATUSES else "success"
        model_training_url = "/model-training"
        job_url = f"{model_training_url}?tab=jobs&job_id={job_id}"
        dataset_url = f"{model_training_url}?tab=datasets&dataset_id={dataset_id}"
        return StepExecutionResult(
            status=step_status,
            outputs={
                "job_result": dict(job),
                "job_id": job_id,
                "dataset_id": dataset_id,
                "training_profile_id": profile_id,
                "training_status": status,
                "training_phase": phase,
                "status": status,
                "phase": phase,
                "terminal": terminal,
                "terminal_result": dict(job.get("result") or {}) if terminal else None,
                "model_training_url": model_training_url,
                "job_url": job_url,
                "dataset_url": dataset_url,
                "links": {
                    "model_training": model_training_url,
                    "job": job_url,
                    "dataset": dataset_url,
                    "api_job": str(job.get("poll_url") or f"/api/ml-intern-training/jobs/{job_id}"),
                    "api_events": str(job.get("events_url") or f"/api/ml-intern-training/jobs/{job_id}/events"),
                },
            },
            diagnostics={
                "job_type": str(job.get("job_type") or "train_lora"),
                "phase": phase,
                "terminal": terminal,
                "idempotent_replay": bool(job.get("idempotent_replay", replayed)),
            },
            warnings=warnings,
            backend_service="MlInternTrainingControlService.create_job",
            executable=True,
            execution_reason=f"vp_adapter: Hub LoRA job status={status} phase={phase}",
        )

    @classmethod
    def _legacy_warnings(cls, metadata: Mapping[str, Any], artifacts: Mapping[str, Any]) -> list[str]:
        present = sorted(key for key in cls._LEGACY_PATH_FIELDS if key in metadata or key in artifacts)
        warnings = []
        if present:
            warnings.append(
                "Deprecated VP path fields detected: "
                + ", ".join(present)
                + ". Use dataset_id; Hub root fields are ignored."
            )
        if "output_dir" in metadata or "outputDir" in metadata:
            warnings.append("Deprecated output_dir detected; use output_name.")
        if "enabled" in metadata or "training_config" in metadata or "trainingConfig" in metadata:
            warnings.append("Step-local training enable/config fields are deprecated and cannot override Hub policy.")
        return warnings


# ── Reranker ──────────────────────────────────────────────────────────────────

class RerankAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "rerank"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.reranker import Reranker
        query = str(artifacts.get("query") or step.metadata.get("query") or "")
        candidates = list(artifacts.get("candidates") or [])
        # ``reranker_weight`` is a read-only compatibility alias. New writes
        # and NodeDefinitions expose only the canonical ``weight`` field.
        weight = float(step.metadata.get("weight", step.metadata.get("reranker_weight", 0.15)))
        enabled = bool(step.metadata.get("enabled", True))
        reranker = Reranker(
            enabled=enabled,
            weight=weight,
            model_digest=_BUILTIN_TOKEN_OVERLAP_RERANKER_DIGEST,
        )
        reranked = reranker.rerank(query=query, candidates=candidates)
        return StepExecutionResult(
            status="success",
            outputs={"reranked": reranked, "count": len(reranked)},
            backend_service="Reranker",
            executable=True,
            execution_reason="vp_adapter: token-overlap boost (deterministic, no LLM)",
        )


# ── Embed API ─────────────────────────────────────────────────────────────────

class EmbedApiAdapter(StepAdapter):
    def __init__(self, *, secret_resolver: Any | None = None) -> None:
        if secret_resolver is None:
            from agent.services.opaque_secret_reference_service import (
                opaque_secret_reference_service,
            )

            secret_resolver = opaque_secret_reference_service
        self._secret_resolver = secret_resolver

    @property
    def kind(self) -> str:
        return "embed_api"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        texts_raw = artifacts.get("texts") or step.metadata.get("texts") or []
        texts: list[str] = [texts_raw] if isinstance(texts_raw, str) else list(texts_raw)
        if not texts:
            return StepExecutionResult(
                status="failed", executable=True,
                execution_reason="embed_api: no texts provided in artifacts['texts'] or metadata['texts']",
                backend_service="EmbeddingProvider",
            )
        provider_name = str(step.metadata.get("provider") or "hash")
        try:
            provider = self._build_provider(step, provider_name)
            embeddings = provider.embed_texts(texts)
        except Exception as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason=f"embed_api: provider={provider_name!r} failed",
                backend_service=f"EmbeddingProvider({provider_name})",
            )
        return StepExecutionResult(
            status="success",
            outputs={"embeddings": embeddings, "count": len(embeddings), "provider": provider_name},
            backend_service=f"EmbeddingProvider({provider_name})",
            executable=True,
            execution_reason=f"vp_adapter: embed_api provider={provider_name!r}",
        )

    def _build_provider(self, step: VisualProcessStep, provider_name: str) -> Any:
        from worker.retrieval.embedding_provider import (
            FakeEmbeddingProvider,
            HashEmbeddingProvider,
            OpenAICompatibleEmbeddingProvider,
        )
        dims = int(step.metadata.get("dimensions") or 12)
        if provider_name in ("hash", ""):
            return HashEmbeddingProvider(dimensions=dims)
        if provider_name == "fake":
            return FakeEmbeddingProvider(dimensions=max(dims, 4))
        if provider_name in ("openai_compatible", "openai"):
            if "api_key" in step.metadata:
                raise ValueError("legacy_plaintext_api_key_quarantined")
            if not bool(step.metadata.get("external_calls_allowed", False)):
                raise ValueError("embedding_provider_external_calls_not_allowed")
            secret_ref = str(step.metadata.get("api_key_secret_ref") or "").strip()
            if not secret_ref:
                raise ValueError("embedding_provider_secret_reference_required")
            api_key = self._secret_resolver.resolve(secret_ref)
            return OpenAICompatibleEmbeddingProvider(
                base_url=str(step.metadata.get("base_url") or ""),
                model=str(step.metadata.get("model") or "text-embedding-3-small"),
                api_key=api_key,
                dimensions=max(dims, 1536),
            )
        raise ValueError(f"Unknown embedding provider: {provider_name!r}")


# ── Sign rotation (TQ-011) ────────────────────────────────────────────────────

class SignRotationAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "sign_rotation"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.turboquant_encoding import DeterministicSignRotation
        vector = list(artifacts.get("vector") or step.metadata.get("vector") or [])
        if not vector:
            return StepExecutionResult(
                status="failed", executable=True,
                execution_reason="sign_rotation: no vector provided",
                backend_service="DeterministicSignRotation",
            )
        seed = int(step.metadata.get("seed", 888))
        rotation = DeterministicSignRotation(seed=seed)
        rotated = rotation.apply(vector)
        return StepExecutionResult(
            status="success",
            outputs={"rotated": rotated, "dim": len(rotated), "seed": seed},
            backend_service="DeterministicSignRotation (TQ-011)",
            executable=True,
            execution_reason="vp_adapter: SHA256 sign-flip, self-inverse, deterministic (production)",
        )


# ── TurboQuant MSE (TQ-012, experimental) ────────────────────────────────────

class TurboQuantMseAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "turboquant_mse"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        from worker.retrieval.turboquant_encoding import TurboQuantMseEncoder
        vector = list(artifacts.get("vector") or step.metadata.get("vector") or [])
        if not vector:
            return StepExecutionResult(
                status="failed", executable=True,
                execution_reason="turboquant_mse: no vector provided",
                backend_service="TurboQuantMseEncoder",
                warnings=["TQ-012 is experimental (no production codebook). TQ-013 ProdStub is a separate unused stub."],
            )
        seed = int(step.metadata.get("seed", 888))
        levels = int(step.metadata.get("levels", 7))
        encoder = TurboQuantMseEncoder(seed=seed, levels=levels)
        encoded = encoder.encode(vector)
        outputs: dict[str, Any] = (
            dict(encoded) if isinstance(encoded, dict) else {"quantized": encoded}
        )
        outputs.update({"seed": seed, "levels": levels})
        return StepExecutionResult(
            status="success",
            outputs=outputs,
            backend_service="TurboQuantMseEncoder (TQ-012)",
            executable=True,
            execution_reason="vp_adapter: sign-rotate + 4-bit scalar quant (experimental PoC, deterministic)",
            warnings=["TQ-012 is experimental (no production codebook). TQ-013 ProdStub is a separate unused stub."],
        )


# ── Workspace snapshot ────────────────────────────────────────────────────────

class WorkspaceSnapshotAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "workspace_snapshot"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        import os
        from pathlib import Path
        try:
            from agent.services.workspace_diff_service import WorkspaceDiffService
        except ImportError as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason="workspace_snapshot: WorkspaceDiffService not available",
                backend_service="WorkspaceDiffService",
            )
        workspace_root = Path(str(
            step.metadata.get("workspace_root")
            or artifacts.get("workspace_root")
            or context.get("workspace_root")
            or os.getcwd()
        ))
        if not workspace_root.exists():
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"workspace_root": str(workspace_root)},
                execution_reason=f"workspace_snapshot: path does not exist: {workspace_root}",
                backend_service="WorkspaceDiffService",
            )
        try:
            svc = WorkspaceDiffService()
            snapshot_id, file_map = svc.take_before_snapshot(workspace_root)
        except Exception as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason=f"workspace_snapshot: take_before_snapshot failed: {exc}",
                backend_service="WorkspaceDiffService",
            )
        return StepExecutionResult(
            status="success",
            outputs={"snapshot_id": snapshot_id, "file_map": file_map, "file_count": len(file_map)},
            backend_service="WorkspaceDiffService.take_before_snapshot",
            executable=True,
            execution_reason="vp_adapter: workspace hash-map snapshot (deterministic, read-only)",
        )


# ── Workspace diff ────────────────────────────────────────────────────────────

class WorkspaceDiffAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "workspace_diff"

    def execute(self, step: VisualProcessStep, artifacts: dict[str, Any], context: dict[str, Any]) -> StepExecutionResult:
        import os
        from pathlib import Path
        try:
            from agent.services.workspace_diff_service import WorkspaceDiffService
        except ImportError as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason="workspace_diff: WorkspaceDiffService not available",
                backend_service="WorkspaceDiffService",
            )
        workspace_root = Path(str(
            step.metadata.get("workspace_root")
            or artifacts.get("workspace_root")
            or context.get("workspace_root")
            or os.getcwd()
        ))
        before_snapshot_id = str(artifacts.get("before_snapshot_id") or "before")
        before_snapshot: dict[str, str] = dict(artifacts.get("before_snapshot") or {})
        after_snapshot_id = str(artifacts.get("after_snapshot_id") or "after")
        after_snapshot: dict[str, str] = dict(artifacts.get("after_snapshot") or {})
        task_id = str(context.get("task_id") or step.id)
        execution_id = str(context.get("execution_id") or "vp-exec")
        try:
            svc = WorkspaceDiffService()
            diff = svc.compute_diff(
                task_id=task_id,
                execution_id=execution_id,
                workspace_root=workspace_root,
                before_snapshot_id=before_snapshot_id,
                before_snapshot=before_snapshot,
                after_snapshot_id=after_snapshot_id,
                after_snapshot=after_snapshot,
            )
            manifest = svc.synthesize_manifest(
                file_change_set=diff,
                workspace_root=workspace_root,
                task_id=task_id,
                goal_id=str(context.get("goal_id") or ""),
                execution_id=execution_id,
                trace_id=str(context.get("trace_id") or ""),
            )
        except Exception as exc:
            return StepExecutionResult(
                status="failed", executable=True,
                diagnostics={"error": str(exc)},
                execution_reason=f"workspace_diff: compute_diff/synthesize_manifest failed: {exc}",
                backend_service="WorkspaceDiffService",
            )
        return StepExecutionResult(
            status="success",
            outputs={"manifest": manifest},
            backend_service="WorkspaceDiffService.compute_diff + synthesize_manifest",
            executable=True,
            execution_reason="vp_adapter: workspace diff → artifact_manifest.v1 (deterministic)",
        )
