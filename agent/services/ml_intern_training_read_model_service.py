from __future__ import annotations

import math
from typing import Any

from agent.db_models import MlInternDatasetDB, MlInternTrainingEventDB, MlInternTrainingJobDB


class MlInternTrainingReadModelService:
    """Pure projections for Angular/API consumers; no local storage paths leak."""

    @staticmethod
    def dataset(dataset: MlInternDatasetDB) -> dict[str, Any]:
        validation = MlInternTrainingReadModelService._validation_summary(dataset.validation_report)
        split = dict(dataset.split_manifest or {})
        metadata = dict(dataset.dataset_metadata or {})
        return {
            "id": dataset.id,
            "name": dataset.name,
            "status": dataset.status,
            "format": dataset.format_type,
            "purpose": metadata.get("purpose"),
            "license": metadata.get("license"),
            "privacy": metadata.get("privacy"),
            "sha256": dataset.content_sha256,
            "size_bytes": dataset.size_bytes,
            "record_count": dataset.record_count,
            "train_record_count": dataset.train_record_count,
            "validation_record_count": dataset.validation_record_count,
            "rejected_record_count": dataset.rejected_record_count,
            "duplicate_record_count": dataset.duplicate_record_count,
            "secret_finding_count": dataset.secret_finding_count,
            "accepted_record_count": validation["accepted_records"],
            "split": {
                **split,
                "train_count": int(split.get("train_record_count") or dataset.train_record_count),
                "validation_count": int(split.get("validation_record_count") or dataset.validation_record_count),
            },
            "validation": validation,
            "validation_status": str(
                (dataset.validation_report or {}).get("status") or ("valid" if validation["valid"] else "pending")
            ),
            "trainable": validation["valid"] and dataset.validation_record_count > 0,
            "version": dataset.version,
            "created_at": dataset.created_at,
            "updated_at": dataset.updated_at,
        }

    @staticmethod
    def job(job: MlInternTrainingJobDB, *, detail: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": job.id,
            "job_id": job.id,
            "task_id": job.task_id,
            "worker_job_id": job.worker_job_id,
            "dataset_id": job.dataset_id,
            "job_type": job.job_type,
            "mode": job.mode,
            "backend": job.backend,
            "base_model": job.base_model,
            "base_model_id": job.base_model,
            "status": job.status,
            "phase": job.phase,
            "progress_percent": job.progress_percent,
            "current_step": job.current_step,
            "max_steps": job.max_steps,
            "epoch": job.epoch,
            "train_loss": job.train_loss,
            "latest_train_loss": job.train_loss,
            "eval_loss": job.eval_loss,
            "latest_eval_loss": job.eval_loss,
            "learning_rate": job.learning_rate,
            "queue_position": job.queue_position,
            "adapter_id": job.adapter_id,
            "checkpoint_ref": MlInternTrainingReadModelService._opaque_ref(job.checkpoint_ref, "checkpoint"),
            "result_ref": job.result_ref,
            "cancellable": job.status in {"queued", "claimed", "running", "cancel_requested"},
            "cancel_mode": (
                str((job.result_summary or {}).get("cancel_mode"))
                if (job.result_summary or {}).get("cancel_mode") in {"cooperative", "forced"}
                else None
            ),
            "error": (
                {
                    "code": job.error_code,
                    "message": (job.error_message or "")[:512],
                    "retryable": job.retryable,
                    "retriable": job.retryable,
                }
                if job.error_code
                else None
            ),
            "version": job.version,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
        if detail:
            payload["result"] = MlInternTrainingReadModelService._safe_result(job.result_summary)
            payload["configuration"] = MlInternTrainingReadModelService._safe_configuration(job.request_spec)
            payload["metrics"] = (
                [
                    {
                        "step": job.current_step,
                        "max_steps": job.max_steps,
                        "epoch": job.epoch,
                        "train_loss": job.train_loss,
                        "eval_loss": job.eval_loss,
                        "learning_rate": job.learning_rate,
                        "recorded_at": job.updated_at,
                    }
                ]
                if job.current_step is not None
                else []
            )
            result = dict(job.result_summary or {})
            artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
            payload["artifact_refs"] = [
                str(item.get("name")) for item in artifacts if isinstance(item, dict) and item.get("name")
            ]
        return payload

    @staticmethod
    def _opaque_ref(value: str | None, prefix: str) -> str | None:
        if not value:
            return None
        import hashlib

        return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _safe_result(value: dict | None) -> dict[str, Any]:
        raw = dict(value or {})
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
        forbidden = {"samples", "records", "prompt", "prompts", "output", "outputs", "logs"}

        def clean(child: Any, depth: int = 0) -> Any:
            if depth > 3 or isinstance(child, list):
                return None
            if child is None or isinstance(child, bool):
                return child
            if isinstance(child, int):
                return child if abs(child) <= 2**63 - 1 else None
            if isinstance(child, float):
                return child if math.isfinite(child) else None
            if isinstance(child, str):
                return child[:128]
            if isinstance(child, dict):
                return {
                    str(key)[:64]: clean(item, depth + 1)
                    for key, item in list(child.items())[:64]
                    if str(key).lower() not in forbidden
                }
            return None

        artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), list) else []
        safe_artifacts: list[dict[str, Any]] = []
        for item in artifacts[:64]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            size = item.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 2**63 - 1:
                continue
            safe_artifacts.append(
                {
                    "name": str(item.get("name") or "")[:191],
                    "sha256": str(item.get("sha256") or "")[:64],
                    "size_bytes": size,
                }
            )
        return {
            "metrics": clean(metrics),
            "cancel_mode": raw.get("cancel_mode") if raw.get("cancel_mode") in {"cooperative", "forced"} else None,
            "artifacts": safe_artifacts,
        }

    @staticmethod
    def event(event: MlInternTrainingEventDB) -> dict[str, Any]:
        payload = dict(event.payload or {})
        metric = {
            "step": payload.get("current_step"),
            "max_steps": payload.get("max_steps"),
            "epoch": payload.get("epoch"),
            "train_loss": payload.get("train_loss"),
            "eval_loss": payload.get("eval_loss"),
            "learning_rate": payload.get("learning_rate"),
            "recorded_at": event.created_at,
        }
        return {
            "id": event.id,
            "sequence": event.sequence,
            "type": event.event_type,
            "event_type": event.event_type,
            "payload": payload,
            "phase": payload.get("phase"),
            "progress_percent": payload.get("progress_percent"),
            "reason_code": payload.get("reason_code"),
            "metric": metric if metric["step"] is not None else None,
            "created_at": event.created_at,
            "timestamp": event.created_at,
        }

    @staticmethod
    def _validation_summary(report: dict | None) -> dict[str, Any]:
        value = dict(report or {})
        return {
            "valid": bool(value.get("ok", value.get("valid", False))),
            "accepted_records": int(value.get("accepted_record_count") or value.get("accepted_records") or 0),
            "rejected_records": int(value.get("rejected_record_count") or value.get("rejected_records") or 0),
            "duplicate_records": int(value.get("duplicate_count") or value.get("duplicate_records") or 0),
            "secret_findings": len(value.get("secret_findings") or []),
            "error_count": len(value.get("errors") or []),
            "warning_count": len(value.get("warnings") or []),
        }

    @staticmethod
    def _safe_configuration(spec: dict | None) -> dict[str, Any]:
        value = dict(spec or {})
        allowed = {
            "dataset_id",
            "job_type",
            "mode",
            "backend",
            "base_model",
            "method",
            "gpu_profile",
            "output_name",
            "hyperparameters",
            "require_dataset_validation",
            "require_secret_scan",
            "adapter_id",
            "eval_dataset_id",
            "scorer_name",
        }
        return {key: value[key] for key in allowed if key in value}


_read_models = MlInternTrainingReadModelService()


def get_ml_intern_training_read_model_service() -> MlInternTrainingReadModelService:
    return _read_models
