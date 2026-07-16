"""Deprecated bounded dry-run and dataset-validation compatibility service.

Live LoRA execution belongs exclusively to the Hub control plane and its
isolated training workers.  This service intentionally cannot launch a model
runner; path-based callers are retained only for additive API compatibility.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.services.ml_intern_dataset_validation_service import (
    MlInternDatasetValidationService,
    get_dataset_validation_service,
)
from agent.services.ml_intern_training_config_service import (
    get_gpu_profile_defaults,
    normalize_ml_intern_training_config,
)


class TrainingJobError(ValueError):
    """Strukturierter Fehler fuer Training-Jobs."""


_DATASET_JOB_TYPES = frozenset({"dataset_validate", "train_lora", "evaluate_lora"})


@dataclass
class TrainingJobResult:
    job_id: str
    job_type: str
    status: str  # dry_run_completed | completed | failed | disabled | validation_failed
    artifact_dir: str | None
    training_summary: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}


class MlInternTrainingJobService:
    """Service fuer LoRA/QLoRA Trainingsjobs."""

    def __init__(
        self,
        training_config: dict | None = None,
        validator: MlInternDatasetValidationService | None = None,
    ) -> None:
        self._training_cfg = normalize_ml_intern_training_config(training_config)
        self._validator = validator or get_dataset_validation_service()

    # --- Public API --------------------------------------------------------

    def submit_job(self, job_spec: dict[str, Any]) -> TrainingJobResult:
        """Validiert und fuehrt einen Trainingsjob aus (oder Dry-Run)."""
        cfg = self._training_cfg
        if not cfg.get("enabled"):
            return TrainingJobResult(
                job_id=self._new_job_id(),
                job_type=str(job_spec.get("job_type") or "unknown"),
                status="disabled",
                artifact_dir=None,
                errors=["ml_intern_training is disabled (enabled=false)"],
            )

        job_type = str(job_spec.get("job_type") or "").strip().lower()
        if not job_type:
            return self._fail(job_spec, "job_type is required")
        if job_type not in set(cfg.get("allowed_job_types") or []):
            return self._fail(
                job_spec,
                f"job_type {job_type!r} not in allowed_job_types: {cfg.get('allowed_job_types')}",
            )

        # Sicherheitspruefungen
        val_errors = self._validate_job_spec(job_spec, cfg)
        if val_errors:
            return TrainingJobResult(
                job_id=self._new_job_id(),
                job_type=job_type,
                status="validation_failed",
                artifact_dir=None,
                errors=val_errors,
            )

        job_id = self._new_job_id()
        artifact_dir = self._make_artifact_dir(cfg["artifact_root"], job_id, job_type)
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        if cfg.get("mode") == "dry_run":
            return self._run_dry(job_id, job_type, job_spec, cfg, artifact_dir, started_at, t0)

        return self._run_live(job_id, job_type, job_spec, cfg, artifact_dir, started_at, t0)

    # --- Dry-Run -----------------------------------------------------------

    def _run_dry(
        self,
        job_id: str,
        job_type: str,
        job_spec: dict,
        cfg: dict,
        artifact_dir: Path,
        started_at: str,
        t0: float,
    ) -> TrainingJobResult:
        warnings: list[str] = []

        if job_type in _DATASET_JOB_TYPES:
            dataset_path = job_spec.get("dataset_path")
            if dataset_path:
                full_dataset = Path(cfg["dataset_root"]) / dataset_path
                if not full_dataset.exists():
                    warnings.append(f"dry_run: dataset_path {full_dataset} does not exist (would fail in live mode)")

        if job_type == "train_lora":
            gpu_warnings = self._check_gpu_params(job_spec, cfg)
            warnings.extend(gpu_warnings)

        summary = {
            "schema": "mlintern_training_summary.v1",
            "job_id": job_id,
            "job_type": job_type,
            "mode": "dry_run",
            "base_model": job_spec.get("base_model"),
            "method": job_spec.get("method", "qlora"),
            "status": "dry_run_completed",
            "config_hash": self._config_hash(job_spec),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "warnings": warnings,
        }
        self._write_artifacts(artifact_dir, summary, job_type="dry_run")

        finished_at = datetime.now(timezone.utc).isoformat()
        return TrainingJobResult(
            job_id=job_id,
            job_type=job_type,
            status="dry_run_completed",
            artifact_dir=str(artifact_dir),
            training_summary=summary,
            warnings=warnings,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # --- Live Run ----------------------------------------------------------

    def _run_live(
        self,
        job_id: str,
        job_type: str,
        job_spec: dict,
        cfg: dict,
        artifact_dir: Path,
        started_at: str,
        t0: float,
    ) -> TrainingJobResult:
        if job_type == "dataset_validate":
            return self._run_dataset_validate(job_id, job_spec, cfg, artifact_dir, started_at, t0)
        return self._fail_result(
            job_id,
            job_type,
            artifact_dir,
            [
                "live_worker_required: submit this operation through the Hub-owned "
                "dataset/job/adapter API so an isolated worker can execute it"
            ],
            started_at,
            t0,
        )

    def _run_dataset_validate(self, job_id, job_spec, cfg, artifact_dir, started_at, t0) -> TrainingJobResult:
        dataset_path = Path(cfg["dataset_root"]) / str(job_spec.get("dataset_path") or "")
        report = self._validator.validate(dataset_path, require_secret_scan=cfg.get("require_secret_scan", True))
        self._validator.write_report(report, artifact_dir / "dataset_validation_report.json")
        status = "completed" if report.ok else "failed"
        self._write_status_file(artifact_dir, status, job_id)
        return TrainingJobResult(
            job_id=job_id,
            job_type="dataset_validate",
            status=status,
            artifact_dir=str(artifact_dir),
            training_summary=report.to_dict(),
            errors=[e.message for e in report.errors if e.severity == "error"],
            warnings=[w.message for w in report.warnings],
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # --- Validation --------------------------------------------------------

    def _validate_job_spec(self, job_spec: dict, cfg: dict) -> list[str]:
        errors: list[str] = []
        job_type = str(job_spec.get("job_type") or "")

        # merge_adapter_optional erfordert allow_merge=true
        if job_type == "merge_adapter_optional" and not job_spec.get("allow_merge"):
            errors.append("merge_adapter_optional requires allow_merge=true")

        # Pfad-Sicherheit fuer output_dir
        output_dir = job_spec.get("output_dir")
        if output_dir:
            artifact_root = Path(cfg.get("artifact_root") or "artifacts/lora").resolve()
            candidate = (artifact_root / str(output_dir)).resolve()
            if not str(candidate).startswith(str(artifact_root)):
                errors.append(f"output_dir '{output_dir}' escapes artifact_root")

        return errors

    def _check_gpu_params(self, job_spec: dict, cfg: dict) -> list[str]:
        """Prueft GPU-Parameter gegen Profil-Limits. Gibt Warnungen/Fehler zurueck."""
        profile = get_gpu_profile_defaults(cfg.get("gpu_profile", "rtx3080-safe"))
        warnings: list[str] = []
        max_bs = profile.get("max_batch_size_hard_limit", 8)
        max_seq = profile.get("max_seq_length_hard_limit", 4096)
        batch = job_spec.get("batch_size")
        seq = job_spec.get("max_seq_length")
        override = job_spec.get("explicit_override") or {}
        override_reason = str(override.get("reason") or "")

        if batch is not None and int(batch) > max_bs:
            if override_reason:
                warnings.append(
                    f"batch_size {batch} exceeds profile limit {max_bs} (override: {override_reason[:100]})"
                )
            else:
                warnings.append(
                    f"batch_size {batch} exceeds profile hard limit {max_bs}; "
                    "provide explicit_override.reason to proceed"
                )
        if seq is not None and int(seq) > max_seq:
            if override_reason:
                warnings.append(
                    f"max_seq_length {seq} exceeds profile limit {max_seq} (override: {override_reason[:100]})"
                )
            else:
                warnings.append(
                    f"max_seq_length {seq} exceeds profile hard limit {max_seq}; "
                    "provide explicit_override.reason to proceed"
                )
        return warnings

    # --- Artefakt-Hilfsmethoden --------------------------------------------

    @staticmethod
    def _make_artifact_dir(artifact_root: str, job_id: str, job_type: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        d = Path(artifact_root) / f"{job_type}_{ts}_{job_id[:8]}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _write_artifacts(artifact_dir: Path, summary: dict, job_type: str) -> None:
        (artifact_dir / "training_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        status = summary.get("status", "unknown")
        (artifact_dir / "status.json").write_text(
            json.dumps({"status": status, "job_type": job_type,
                        "job_id": summary.get("job_id"), "finished_at": summary.get("finished_at")}, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_status_file(artifact_dir: Path, status: str, job_id: str) -> None:
        (artifact_dir / "status.json").write_text(
            json.dumps({"status": status, "job_id": job_id,
                        "finished_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _config_hash(spec: dict) -> str:
        safe = {k: v for k, v in sorted(spec.items()) if k not in ("started_at", "finished_at", "job_id")}
        return hashlib.sha256(json.dumps(safe, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _new_job_id() -> str:
        return f"job-{uuid.uuid4()}"

    @staticmethod
    def _fail(job_spec: dict, msg: str) -> TrainingJobResult:
        return TrainingJobResult(
            job_id=f"job-{uuid.uuid4()}",
            job_type=str(job_spec.get("job_type") or "unknown"),
            status="validation_failed",
            artifact_dir=None,
            errors=[msg],
        )

    @staticmethod
    def _fail_result(job_id, job_type, artifact_dir, errors, started_at, t0) -> TrainingJobResult:
        finished = datetime.now(timezone.utc).isoformat()
        MlInternTrainingJobService._write_status_file(artifact_dir, "failed", job_id)
        return TrainingJobResult(
            job_id=job_id, job_type=job_type, status="failed",
            artifact_dir=str(artifact_dir), errors=errors,
            started_at=started_at, finished_at=finished,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


_service_instance: MlInternTrainingJobService | None = None


def get_training_job_service(training_config: dict | None = None) -> MlInternTrainingJobService:
    global _service_instance
    if training_config is not None:
        return MlInternTrainingJobService(training_config)
    if _service_instance is None:
        _service_instance = MlInternTrainingJobService()
    return _service_instance
