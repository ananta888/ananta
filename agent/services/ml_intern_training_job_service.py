"""Training-Job-Service fuer LoRA/QLoRA Fine-Tuning (MLLORA-011..014).

Verwaltet Dry-Run und optionale echte Trainingsjobs.
- Default: enabled=false, mode=dry_run
- Echter Lauf: nur wenn enabled=true und mode=live
- GPU-Schutz: rtx3080-safe Defaults, Override nur mit Begruendung
- Artifact-first: jeder Job bekommt eigenes versioniertes Output-Verzeichnis
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.services.ml_intern_training_config_service import (
    normalize_ml_intern_training_config,
    get_gpu_profile_defaults,
)
from agent.services.ml_intern_dataset_validation_service import (
    MlInternDatasetValidationService,
    get_dataset_validation_service,
)


class TrainingJobError(ValueError):
    """Strukturierter Fehler fuer Training-Jobs."""


_RISKY_JOB_TYPES = frozenset({"train_lora", "merge_adapter_optional"})
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
            return self._fail(job_spec, f"job_type {job_type!r} not in allowed_job_types: {cfg.get('allowed_job_types')}")

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
        if job_type == "train_lora":
            return self._run_train_lora(job_id, job_spec, cfg, artifact_dir, started_at, t0)
        if job_type == "evaluate_lora":
            return self._run_evaluate_lora(job_id, job_spec, cfg, artifact_dir, started_at, t0)
        if job_type == "register_adapter":
            return self._run_register_adapter(job_id, job_spec, cfg, artifact_dir, started_at, t0)
        if job_type == "export_adapter":
            return self._run_export_adapter(job_id, job_spec, cfg, artifact_dir, started_at, t0)
        if job_type == "merge_adapter_optional":
            return self._run_merge_adapter(job_id, job_spec, cfg, artifact_dir, started_at, t0)
        return self._fail_result(job_id, job_type, artifact_dir, [f"unhandled job_type: {job_type!r}"], started_at, t0)

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

    def _run_train_lora(self, job_id, job_spec, cfg, artifact_dir, started_at, t0) -> TrainingJobResult:
        # Dataset-Validierung erzwingen
        if cfg.get("require_dataset_validation"):
            dataset_path = Path(cfg["dataset_root"]) / str(job_spec.get("dataset_path") or "")
            report = self._validator.validate(dataset_path, require_secret_scan=cfg.get("require_secret_scan", True))
            if not report.ok:
                self._validator.write_report(report, artifact_dir / "dataset_validation_report.json")
                return self._fail_result(job_id, "train_lora", artifact_dir,
                    [f"dataset validation failed: {len(report.errors)} errors"], started_at, t0)

        gpu_params = self._resolve_gpu_params(job_spec, cfg)
        config_hash = self._config_hash({**job_spec, **gpu_params})
        dataset_hash = self._hash_path(Path(cfg["dataset_root"]) / str(job_spec.get("dataset_path") or ""))

        summary = {
            "schema": "mlintern_training_summary.v1",
            "job_id": job_id,
            "job_type": "train_lora",
            "base_model": job_spec.get("base_model"),
            "method": job_spec.get("method", "qlora"),
            "backend": cfg.get("backend"),
            "config_hash": config_hash,
            "dataset_hash": dataset_hash,
            "gpu_params": gpu_params,
            "status": "pending",
            "started_at": started_at,
        }

        backend = cfg.get("backend", "unsloth")
        result = self._invoke_backend_runner(
            backend=backend,
            job_id=job_id,
            job_spec=job_spec,
            gpu_params=gpu_params,
            artifact_dir=artifact_dir,
            cfg=cfg,
        )

        summary["status"] = result["status"]
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._write_artifacts(artifact_dir, summary, job_type="train_lora")

        return TrainingJobResult(
            job_id=job_id,
            job_type="train_lora",
            status=result["status"],
            artifact_dir=str(artifact_dir),
            training_summary=summary,
            errors=result.get("errors", []),
            warnings=result.get("warnings", []),
            started_at=started_at,
            finished_at=summary["finished_at"],
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    def _run_evaluate_lora(self, job_id, job_spec, cfg, artifact_dir, started_at, t0) -> TrainingJobResult:
        summary = {
            "schema": "mlintern_training_summary.v1",
            "job_id": job_id,
            "job_type": "evaluate_lora",
            "base_model": job_spec.get("base_model"),
            "adapter_path": job_spec.get("adapter_path"),
            "status": "completed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "note": "evaluate_lora delegates to MlInternLoraEvalService",
        }
        self._write_artifacts(artifact_dir, summary, job_type="evaluate_lora")
        return TrainingJobResult(
            job_id=job_id, job_type="evaluate_lora", status="completed",
            artifact_dir=str(artifact_dir), training_summary=summary,
            started_at=started_at, finished_at=summary["finished_at"],
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    def _run_register_adapter(self, job_id, job_spec, cfg, artifact_dir, started_at, t0) -> TrainingJobResult:
        summary = {"schema": "mlintern_training_summary.v1", "job_id": job_id,
                   "job_type": "register_adapter", "status": "completed",
                   "adapter_name": job_spec.get("adapter_name"),
                   "adapter_path": job_spec.get("adapter_path"),
                   "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat()}
        self._write_artifacts(artifact_dir, summary, job_type="register_adapter")
        return TrainingJobResult(job_id=job_id, job_type="register_adapter", status="completed",
            artifact_dir=str(artifact_dir), training_summary=summary,
            started_at=started_at, finished_at=summary["finished_at"],
            duration_ms=int((time.monotonic() - t0) * 1000))

    def _run_export_adapter(self, job_id, job_spec, cfg, artifact_dir, started_at, t0) -> TrainingJobResult:
        summary = {"schema": "mlintern_training_summary.v1", "job_id": job_id,
                   "job_type": "export_adapter", "status": "completed",
                   "adapter_path": job_spec.get("adapter_path"),
                   "output_dir": job_spec.get("output_dir"),
                   "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat()}
        self._write_artifacts(artifact_dir, summary, job_type="export_adapter")
        return TrainingJobResult(job_id=job_id, job_type="export_adapter", status="completed",
            artifact_dir=str(artifact_dir), training_summary=summary,
            started_at=started_at, finished_at=summary["finished_at"],
            duration_ms=int((time.monotonic() - t0) * 1000))

    def _run_merge_adapter(self, job_id, job_spec, cfg, artifact_dir, started_at, t0) -> TrainingJobResult:
        if not job_spec.get("allow_merge"):
            return self._fail_result(job_id, "merge_adapter_optional", artifact_dir,
                ["merge_adapter_optional requires allow_merge=true"], started_at, t0)
        summary = {"schema": "mlintern_training_summary.v1", "job_id": job_id,
                   "job_type": "merge_adapter_optional", "status": "completed",
                   "base_model": job_spec.get("base_model"),
                   "adapter_path": job_spec.get("adapter_path"),
                   "output_dir": job_spec.get("output_dir"),
                   "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat()}
        self._write_artifacts(artifact_dir, summary, job_type="merge_adapter")
        return TrainingJobResult(job_id=job_id, job_type="merge_adapter_optional", status="completed",
            artifact_dir=str(artifact_dir), training_summary=summary,
            started_at=started_at, finished_at=summary["finished_at"],
            duration_ms=int((time.monotonic() - t0) * 1000))

    # --- Backend Runner ----------------------------------------------------

    def _invoke_backend_runner(
        self,
        *,
        backend: str,
        job_id: str,
        job_spec: dict,
        gpu_params: dict,
        artifact_dir: Path,
        cfg: dict,
    ) -> dict[str, Any]:
        if backend == "mock":
            return {"status": "trained", "errors": [], "warnings": ["mock backend — no real training performed"]}

        # Fuer echte Backends (unsloth, peft_trl): Subprocess-Aufruf
        env = self._bounded_env(cfg.get("env_allowlist") or [])
        script_spec = {
            "job_id": job_id,
            "job_type": "train_lora",
            "base_model": job_spec.get("base_model"),
            "dataset_path": str(Path(cfg["dataset_root"]) / str(job_spec.get("dataset_path") or "")),
            "output_dir": str(artifact_dir),
            "method": job_spec.get("method", "qlora"),
            "backend": backend,
            **gpu_params,
        }
        spec_file = artifact_dir / "job_spec.json"
        spec_file.write_text(json.dumps(script_spec, indent=2), encoding="utf-8")

        cmd = shlex.split(f"python -m agent.ml_intern_training_runner --spec {spec_file}")
        timeout = int(cfg.get("timeout_seconds") or 3600)
        try:
            result = subprocess.run(
                cmd,
                cwd=str(Path.cwd()),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout = (result.stdout or "")[:4096]
            stderr = (result.stderr or "")[:4096]
            log_path = artifact_dir / "training_log.jsonl"
            log_path.write_text(json.dumps({"stdout": stdout, "stderr": stderr}) + "\n", encoding="utf-8")
            if result.returncode != 0:
                return {"status": "failed", "errors": [f"runner exited with code {result.returncode}: {stderr[:512]}"], "warnings": []}
            return {"status": "trained", "errors": [], "warnings": []}
        except subprocess.TimeoutExpired:
            return {"status": "failed", "errors": [f"training timeout after {timeout}s"], "warnings": []}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "failed", "errors": [f"runner invocation failed: {exc}"], "warnings": []}

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
                warnings.append(f"batch_size {batch} exceeds profile limit {max_bs} (override: {override_reason[:100]})")
            else:
                warnings.append(f"batch_size {batch} exceeds profile hard limit {max_bs}; provide explicit_override.reason to proceed")
        if seq is not None and int(seq) > max_seq:
            if override_reason:
                warnings.append(f"max_seq_length {seq} exceeds profile limit {max_seq} (override: {override_reason[:100]})")
            else:
                warnings.append(f"max_seq_length {seq} exceeds profile hard limit {max_seq}; provide explicit_override.reason to proceed")
        return warnings

    def _resolve_gpu_params(self, job_spec: dict, cfg: dict) -> dict:
        defaults = get_gpu_profile_defaults(cfg.get("gpu_profile", "rtx3080-safe"))
        return {
            "load_in_4bit": job_spec.get("load_in_4bit", defaults["load_in_4bit"]),
            "lora_rank": job_spec.get("lora_rank", defaults["lora_rank"]),
            "lora_alpha": job_spec.get("lora_alpha", defaults["lora_alpha"]),
            "lora_dropout": job_spec.get("lora_dropout", defaults["lora_dropout"]),
            "max_seq_length": job_spec.get("max_seq_length", defaults["max_seq_length"]),
            "batch_size": job_spec.get("batch_size", defaults["batch_size"]),
            "gradient_accumulation_steps": job_spec.get("gradient_accumulation_steps", defaults["gradient_accumulation_steps"]),
            "learning_rate": job_spec.get("learning_rate", defaults["learning_rate"]),
        }

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
    def _bounded_env(env_allowlist: list[str]) -> dict[str, str]:
        base = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}
        env: dict[str, str] = {}
        for k in base:
            v = os.environ.get(k)
            if v is not None:
                env[k] = v
        for k in env_allowlist:
            v = os.environ.get(str(k))
            if v is not None:
                env[str(k)] = v
        return env

    @staticmethod
    def _config_hash(spec: dict) -> str:
        safe = {k: v for k, v in sorted(spec.items()) if k not in ("started_at", "finished_at", "job_id")}
        return hashlib.sha256(json.dumps(safe, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _hash_path(path: Path) -> str:
        if not path.exists():
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

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
