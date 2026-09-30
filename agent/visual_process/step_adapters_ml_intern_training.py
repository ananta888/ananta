"""ML-Intern LoRA training step adapter.

``ml_intern_train_lora`` materializes a VP step as the canonical Hub-owned
asynchronous training job via ``MlInternTrainingControlService.create_job``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.step_adapters_ml_intern_dataset import (
    MlInternLegacyDatasetImportAdapter,
)
from agent.visual_process.step_adapters_ml_intern_support import (
    ControlFactory,
    LegacyDatasetImportFactory,
    MlInternVisualProcessAdapterError,
    _ml_intern_training_config,
    _ml_intern_training_principal,
    _MlInternTrainingControlPort,
)
from agent.visual_process.step_executor import StepAdapter, StepExecutionResult


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
