"""Validation and projection policies for the Hub-owned LoRA control service."""

from __future__ import annotations

import math
from typing import Any, Mapping

from agent.db_models import MlInternTrainingJobDB
from agent.services.ml_intern_training_config_service import get_gpu_profile_defaults
from agent.services.ml_intern_training_contract import (
    CreateTrainingJobCommand,
    MlInternTrainingContractError,
    assert_job_transition,
    normalize_run_ids,
    normalize_source_ids,
    sanitize_event_payload,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from ananta_contracts.unsloth_capability import (
    UnslothWorkerCapabilityContractError,
    validate_worker_capability_probe,
)


class MlInternTrainingControlPolicyMixin:
    """Keep request policy and safe projection logic separate from admission."""

    def _transition(
        self,
        principal: MlInternTrainingPrincipal,
        job: MlInternTrainingJobDB,
        target: str,
        *,
        phase: str,
        progress_percent: float,
    ) -> MlInternTrainingJobDB:
        assert_job_transition(job.status, target)
        expected = job.version
        now = self._clock()
        job.status = target
        job.phase = phase
        job.progress_percent = max(job.progress_percent, min(100.0, max(0.0, progress_percent)))
        if target == "running" and job.started_at is None:
            job.started_at = now
        if target in {"cancelled", "completed", "failed"}:
            job.finished_at = now
        saved = self._repository.save_job(job, expected_version=expected)
        self._repository.append_event(
            principal,
            saved.id,
            event_type=target,
            dedupe_key=f"transition-{target}-{saved.version}",
            payload=sanitize_event_payload(
                {
                    "status": target,
                    "phase": phase,
                    "progress_percent": saved.progress_percent,
                    "reason_code": saved.error_code,
                    "adapter_id": saved.adapter_id,
                    "cancel_mode": (saved.result_summary or {}).get("cancel_mode"),
                }
            ),
        )
        try:
            from agent.services.task_runtime_service import update_local_task_status

            task_status = {
                "running": "in_progress",
                "completed": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(target)
            if task_status:
                update_local_task_status(
                    saved.task_id,
                    task_status,
                    event_type=f"ml_intern_training_{target}",
                    event_actor="hub",
                    event_details={"job_id": saved.id, "reason_code": saved.error_code},
                )
        except Exception:
            pass
        return saved

    @staticmethod
    def _max_steps(spec: Mapping[str, Any]) -> int | None:
        hyperparameters = spec.get("hyperparameters")
        if not isinstance(hyperparameters, Mapping) or hyperparameters.get("max_steps") is None:
            return None
        return int(hyperparameters["max_steps"])

    def _configured_model_ids(self) -> set[str]:
        catalog = self._config.get("base_model_catalog")
        if isinstance(catalog, Mapping):
            return {str(value) for value in catalog}
        return {str(value) for value in self._config.get("base_models") or []}

    def _bind_dataset_provenance(self, command: CreateTrainingJobCommand, dataset: Any) -> None:
        metadata = dict(dataset.dataset_metadata or {})
        dataset_hash = str(dataset.content_sha256 or metadata.get("dataset_sha256") or "").strip().lower()
        if len(dataset_hash) != 64 or any(character not in "0123456789abcdef" for character in dataset_hash):
            raise MlInternTrainingContractError(
                "dataset_hash_unverified",
                "training requires the canonical dataset SHA-256 supplied by the dataset catalog",
                status_code=409,
            )

        dataset_source_ids = normalize_source_ids(metadata.get("source_ids"))
        dataset_run_ids = normalize_run_ids(metadata.get("run_ids"))
        requested_source_ids = normalize_source_ids(command.request_spec.get("source_ids"))
        requested_run_ids = normalize_run_ids(command.request_spec.get("run_ids"))
        if requested_source_ids and requested_source_ids != dataset_source_ids:
            raise MlInternTrainingContractError(
                "source_id_unverified",
                "provided source IDs are not bound to the canonical dataset version",
                status_code=409,
            )
        if requested_run_ids and requested_run_ids != dataset_run_ids:
            raise MlInternTrainingContractError(
                "run_id_unverified",
                "provided run IDs are not bound to the canonical dataset version",
                status_code=409,
            )

        security = dict(self._config.get("unsloth_security") or {})
        trusted_source_ids = set(normalize_source_ids(security.get("trusted_source_ids")))
        trusted_run_ids = set(normalize_run_ids(security.get("trusted_run_ids")))
        if trusted_source_ids and any(identifier not in trusted_source_ids for identifier in dataset_source_ids):
            raise MlInternTrainingContractError(
                "source_id_unverified",
                "dataset source ID is unknown to the configured provenance authority",
                status_code=409,
            )
        if trusted_run_ids and any(identifier not in trusted_run_ids for identifier in dataset_run_ids):
            raise MlInternTrainingContractError(
                "run_id_unverified",
                "dataset run ID is unknown to the configured provenance authority",
                status_code=409,
            )

        provenance_verified = (
            metadata.get("provenance_verified") is True and bool(dataset_source_ids) and bool(dataset_run_ids)
        )
        if security.get("require_grounded_provenance") is True and not provenance_verified:
            raise MlInternTrainingContractError(
                "grounded_provenance_required",
                "training policy requires verified provided SRC_* and RUN_* bindings",
                status_code=409,
            )

        command.request_spec["dataset_hash"] = dataset_hash
        command.request_spec["provenance_status"] = "verified" if provenance_verified else "unverified"
        if dataset_source_ids:
            command.request_spec["source_ids"] = list(dataset_source_ids)
        else:
            command.request_spec.pop("source_ids", None)
        if dataset_run_ids:
            command.request_spec["run_ids"] = list(dataset_run_ids)
        else:
            command.request_spec.pop("run_ids", None)

    def _worker_capability_probe(self) -> Mapping[str, Any] | None:
        if self._execution_port is None:
            return None
        probe = getattr(self._execution_port, "capability_probe", None)
        if not callable(probe):
            return None
        try:
            return validate_worker_capability_probe(probe())
        except (RuntimeError, TypeError, UnslothWorkerCapabilityContractError):
            return None

    def _worker_supports(
        self,
        job_type: str,
        backend: str,
        gpu_profile: str | None = None,
        *,
        probe: Mapping[str, Any] | None = None,
    ) -> bool:
        snapshot = probe if probe is not None else self._worker_capability_probe()
        if snapshot is None:
            return False
        backend_state = snapshot["backends"].get(backend)
        if (
            not isinstance(backend_state, Mapping)
            or backend_state.get("available") is not True
            or job_type not in backend_state.get("operations", ())
        ):
            return False
        if gpu_profile is None:
            return True
        profile_state = snapshot["gpu_profiles"].get(gpu_profile)
        return isinstance(profile_state, Mapping) and profile_state.get("available") is True

    def _requested_gpu_profile(self, command: CreateTrainingJobCommand) -> str:
        explicit = str(command.request_spec.get("gpu_profile") or "").strip().lower()
        if explicit:
            return explicit
        if command.backend == "mock":
            return "none"
        return str(self._config.get("gpu_profile") or "rtx3080-safe")

    def _assert_request_policy(self, command: CreateTrainingJobCommand, gpu_profile: str) -> None:
        for flag in ("require_dataset_validation", "require_secret_scan"):
            if self._config.get(flag, True) and command.request_spec.get(flag) is False:
                raise MlInternTrainingContractError(
                    "training_policy_override_denied",
                    f"{flag}=false cannot weaken the Hub safety policy",
                    status_code=403,
                )
        profile = get_gpu_profile_defaults(gpu_profile)
        values = command.request_spec.get("hyperparameters")
        hyperparameters = dict(values) if isinstance(values, Mapping) else {}
        batch_size = int(hyperparameters.get("batch_size") or profile.get("batch_size") or 1)
        max_sequence_length = int(hyperparameters.get("max_seq_length") or profile.get("max_seq_length") or 512)
        max_batch_size = int(profile.get("max_batch_size_hard_limit") or 1)
        max_profile_sequence = int(profile.get("max_seq_length_hard_limit") or 512)
        if batch_size > max_batch_size:
            raise MlInternTrainingContractError(
                "gpu_profile_batch_size_exceeded",
                f"batch_size exceeds the {gpu_profile} hard limit of {max_batch_size}",
            )
        if max_sequence_length > max_profile_sequence:
            raise MlInternTrainingContractError(
                "gpu_profile_sequence_length_exceeded",
                f"max_seq_length exceeds the {gpu_profile} hard limit of {max_profile_sequence}",
            )
        bounded_parameters = (
            (
                "gradient_accumulation_steps",
                int(profile.get("max_gradient_accumulation_steps_hard_limit") or 1),
            ),
            ("lora_rank", int(profile.get("max_lora_rank_hard_limit") or 1)),
            ("lora_alpha", int(profile.get("max_lora_alpha_hard_limit") or 1)),
        )
        for field, maximum in bounded_parameters:
            value = int(hyperparameters.get(field) or profile.get(field) or 1)
            if value > maximum:
                raise MlInternTrainingContractError(
                    "gpu_profile_adapter_parameter_exceeded",
                    f"{field} exceeds the {gpu_profile} hard limit of {maximum}",
                )
        dropout = float(hyperparameters.get("lora_dropout", profile.get("lora_dropout") or 0.0))
        if dropout > float(profile.get("max_lora_dropout_hard_limit") or 0.0):
            raise MlInternTrainingContractError(
                "gpu_profile_adapter_parameter_exceeded",
                f"lora_dropout exceeds the {gpu_profile} hard limit",
            )
        target_modules = hyperparameters.get("target_modules")
        if isinstance(target_modules, list) and len(target_modules) > int(
            profile.get("max_target_modules_hard_limit") or 1
        ):
            raise MlInternTrainingContractError(
                "gpu_profile_adapter_parameter_exceeded",
                f"target_modules exceeds the {gpu_profile} hard limit",
            )
        requires_4bit = profile.get("required_quantization") == "4bit"
        requested_4bit = bool(
            hyperparameters.get(
                "load_in_4bit",
                str(command.request_spec.get("method") or "").strip().lower() == "qlora",
            )
        )
        if requires_4bit and not requested_4bit:
            raise MlInternTrainingContractError(
                "gpu_profile_quantization_required",
                f"{gpu_profile} requires 4bit quantization",
            )

    @staticmethod
    def _secret_scan_passed(validation: Mapping[str, Any]) -> bool:
        reports = [validation.get("train")]
        if validation.get("validation") is not None:
            reports.append(validation.get("validation"))
        structured = [report for report in reports if isinstance(report, Mapping)]
        if structured:
            return all(report.get("secret_scan_passed") is True for report in structured)
        # Compatibility for pre-catalog validation reports.
        return validation.get("secret_scan_passed") is True or (
            validation.get("ok") is True
            and int(validation.get("secret_finding_count") or 0) == 0
            and not validation.get("secret_findings")
        )

    @staticmethod
    def _device_for_gpu_profile(gpu_profile: str) -> str:
        return "cpu" if gpu_profile == "none" else "nvidia"

    @classmethod
    def _safe_result_summary(cls, result: Mapping[str, Any]) -> dict[str, Any]:
        forbidden_keys = {
            "samples",
            "records",
            "prompt",
            "prompts",
            "output",
            "outputs",
            "base_output",
            "adapter_output",
            "logs",
        }

        def clean_metric(value: Any, *, depth: int = 0) -> Any:
            if depth > 3:
                return None
            if value is None or isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value if abs(value) <= 2**63 - 1 else None
            if isinstance(value, float):
                return value if math.isfinite(value) else None
            if isinstance(value, str):
                return value[:128]
            if isinstance(value, Mapping):
                return {
                    str(key)[:64]: clean_metric(child, depth=depth + 1)
                    for key, child in list(value.items())[:64]
                    if str(key).strip().lower() not in forbidden_keys
                }
            return None

        summary: dict[str, Any] = {}
        metrics = result.get("metrics")
        if isinstance(metrics, Mapping):
            summary["metrics"] = clean_metric(metrics)
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            admitted: list[dict[str, Any]] = []
            for item in artifacts[:64]:
                if not isinstance(item, Mapping) or not item.get("name"):
                    continue
                size = item.get("size_bytes")
                if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 2**63 - 1:
                    continue
                admitted.append(
                    {
                        "name": str(item.get("name") or "")[:191],
                        "sha256": str(item.get("sha256") or "")[:64],
                        "size_bytes": size,
                    }
                )
            summary["artifacts"] = admitted
        if result.get("resume_checkpoint") is not None:
            summary["resume_checkpoint"] = cls._normalize_resume_checkpoint(result["resume_checkpoint"])
        if result.get("cancel_mode") in {"cooperative", "forced"}:
            summary["cancel_mode"] = result["cancel_mode"]
        return summary

    @staticmethod
    def _legacy_spec(spec: Mapping[str, Any], dataset_path: str) -> dict[str, Any]:
        hyperparameters = dict(spec.get("hyperparameters") or {})
        return {
            "job_type": str(spec.get("job_type") or "train_lora"),
            "base_model": spec.get("base_model"),
            "dataset_path": dataset_path,
            "method": str(spec.get("method") or "qlora"),
            "output_dir": str(spec.get("output_name") or "adapter"),
            **hyperparameters,
        }

    def _accepted_read_model(self, job: MlInternTrainingJobDB, *, replayed: bool) -> dict[str, Any]:
        payload = self._read_models.job(job, detail=True)
        payload.update(
            {
                "idempotent_replay": replayed,
                "poll_url": f"/api/ml-intern-training/jobs/{job.id}",
                "events_url": f"/api/ml-intern-training/jobs/{job.id}/events",
            }
        )
        return payload
